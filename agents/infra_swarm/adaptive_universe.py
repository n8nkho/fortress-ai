"""Adaptive universe selection — promote winners, drop toxic symbols, balance layers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.infra_swarm_config import (
    adaptive_universe_max_active,
    adaptive_universe_max_per_layer,
    adaptive_universe_min_active,
    candidate_pool,
    layer_for_symbol,
    layer_symbols,
    lifetime_pause_min_exits,
    lifetime_pause_min_pnl_usd,
    read_active_universe,
    runtime_denylist,
    save_active_universe,
    swarm_data_dir,
    symbol_pause_win_rate,
)


def _learned_path(symbol: str) -> Path:
    sym = symbol.upper().replace(".", "_")
    return swarm_data_dir() / "learned" / f"{sym}.json"


def _symbol_score(symbol: str) -> float:
    """Higher = keep/promote in active universe."""
    p = _learned_path(symbol)
    if not p.exists():
        return 0.15
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    params = data.get("params") or {}
    if params.get("pause_entries"):
        return -2.0
    lt = data.get("lifetime_stats") or {}
    ss = data.get("session_stats") or {}
    lt_ex = int(lt.get("exits") or 0)
    lt_pnl = float(lt.get("sum_pnl_usd") or 0)
    ss_ex = int(ss.get("exits") or 0)
    ss_pnl = float(ss.get("sum_pnl_usd") or 0)
    lt_w = int(lt.get("wins") or 0)
    lt_l = int(lt.get("losses") or 0)
    wr = lt_w / max(lt_w + lt_l, 1)
    score = 0.0
    if lt_ex >= lifetime_pause_min_exits() and lt_pnl <= lifetime_pause_min_pnl_usd() and wr < symbol_pause_win_rate():
        return -3.0
    if lt_ex >= 3:
        score += lt_pnl / max(lt_ex, 1) * 4.0
        score += (wr - 0.5) * 0.5
    if ss_ex >= 2:
        score += ss_pnl / max(ss_ex, 1) * 2.0
    score += min(lt_ex, 20) * 0.01
    return round(score, 4)


def refresh_adaptive_universe(*, force: bool = False) -> dict[str, Any]:
    """Re-score candidate pool; rewrite active universe with layer balance."""
    pool = [s for s in candidate_pool() if s not in runtime_denylist()]
    scored = sorted(((s, _symbol_score(s)) for s in pool), key=lambda x: x[1], reverse=True)
    min_active = adaptive_universe_min_active()
    max_active = adaptive_universe_max_active()
    max_per = adaptive_universe_max_per_layer()
    current = read_active_universe()
    if not force and current and len(current) >= min_active:
        # Light refresh: drop negative scores, backfill from pool
        kept = [s for s in current if _symbol_score(s) > -0.5 and s in pool]
    else:
        kept = []

    selected: list[str] = []
    layer_counts: dict[str, int] = {"L1": 0, "L2": 0, "L3": 0, "L4": 0}

    def _try_add(sym: str) -> bool:
        if sym in selected:
            return False
        layer = layer_for_symbol(sym)
        if layer_counts.get(layer, 0) >= max_per:
            return False
        selected.append(sym)
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        return True

    for sym in kept:
        _try_add(sym)

    for sym, sc in scored:
        if len(selected) >= max_active:
            break
        if sc < -0.25:
            continue
        _try_add(sym)

    # Ensure at least one symbol per layer when available
    for layer in ("L1", "L2", "L3", "L4"):
        if layer_counts.get(layer, 0) > 0:
            continue
        for sym in layer_symbols(layer):
            if sym in pool and _try_add(sym):
                break

    while len(selected) < min_active:
        added = False
        for sym, sc in scored:
            if sym not in selected and sc > -1.0:
                if _try_add(sym):
                    added = True
                    break
        if not added or len(selected) >= max_active:
            break

    selected = selected[:max_active]
    meta = {
        "scores": {s: _symbol_score(s) for s in selected},
        "layer_counts": {k: sum(1 for x in selected if layer_for_symbol(x) == k) for k in ("L1", "L2", "L3", "L4")},
        "candidate_pool_size": len(pool),
    }
    save_active_universe(selected, meta=meta)
    return {"active": selected, **meta, "refreshed_utc": datetime.now(timezone.utc).isoformat()}
