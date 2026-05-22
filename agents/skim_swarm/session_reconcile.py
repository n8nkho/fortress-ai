"""Rebuild session_stats from decisions.jsonl so learning matches the full ET session."""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from agents.skim_swarm.eod import session_date_et
from agents.skim_swarm.pnl import _wave_session_date
from agents.skim_swarm.symbol_learning import (
    _DEFAULT_SESSION_STATS,
    _empty_pattern_stats,
    _entry_pattern_from_reasoning,
    _learned_lock,
    catch_up_improvement,
    load_learned,
    refresh_adaptive_params,
    save_learned,
)
from utils.skim_swarm_config import normalize_symbol, swarm_data_dir, universe

logger = logging.getLogger("skim_swarm.session_reconcile")

_RECONCILE_MARKER = "session_reconcile_applied"


def _fresh_session_stats() -> dict[str, Any]:
    return deepcopy(_DEFAULT_SESSION_STATS)


def _fresh_pattern_stats() -> dict[str, dict[str, Any]]:
    return _empty_pattern_stats()


def aggregate_session_from_decisions(session: str | None = None) -> dict[str, dict[str, Any]]:
    """Scan decisions.jsonl and rebuild per-symbol session_stats + pattern_stats."""
    sess = session or session_date_et()
    path = swarm_data_dir() / "decisions.jsonl"
    out: dict[str, dict[str, Any]] = {}
    last_entry: dict[str, dict[str, str | None]] = {}

    def _sym_row(sym: str) -> dict[str, Any]:
        sym = normalize_symbol(sym)
        if sym not in out:
            out[sym] = {
                "session_stats": _fresh_session_stats(),
                "pattern_stats": _fresh_pattern_stats(),
            }
        return out[sym]

    if not path.exists():
        return out

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            wave = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _wave_session_date(str(wave.get("ts") or "")) != sess:
            continue
        for row in wave.get("results") or []:
            sym = normalize_symbol(str(row.get("symbol") or ""))
            if not sym:
                continue
            bucket = _sym_row(sym)
            stats = bucket["session_stats"]
            stats["decisions"] = int(stats.get("decisions") or 0) + 1

            act = row.get("act") or {}
            dec = row.get("decision") or {}
            if not act.get("executed"):
                continue

            action = str(dec.get("action") or "").lower()
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            reasoning = dec.get("reasoning")

            if action in ("enter_long", "enter_short"):
                stats["entries"] = int(stats.get("entries") or 0) + 1
                side = "long" if action == "enter_long" else "short"
                pattern = _entry_pattern_from_reasoning(str(reasoning or ""))
                last_entry[sym] = {"pattern": pattern, "side": side}
                continue

            if action not in ("exit_position", "flatten"):
                continue

            pnl_raw = features.get("unrealized_usd")
            if pnl_raw is None:
                continue
            pnl = float(pnl_raw)
            stats["exits"] = int(stats.get("exits") or 0) + 1
            stats["sum_pnl_usd"] = round(float(stats.get("sum_pnl_usd") or 0) + pnl, 4)
            if pnl >= 0:
                stats["wins"] = int(stats.get("wins") or 0) + 1
            else:
                stats["losses"] = int(stats.get("losses") or 0) + 1

            ent = last_entry.get(sym) or {}
            side = str(ent.get("side") or features.get("side") or "")
            if side == "long":
                stats["long_pnl_usd"] = round(float(stats.get("long_pnl_usd") or 0) + pnl, 4)
                stats["long_exits"] = int(stats.get("long_exits") or 0) + 1
            elif side == "short":
                stats["short_pnl_usd"] = round(float(stats.get("short_pnl_usd") or 0) + pnl, 4)
                stats["short_exits"] = int(stats.get("short_exits") or 0) + 1

            pattern = ent.get("pattern")
            if pattern and pattern in bucket["pattern_stats"]:
                ps = bucket["pattern_stats"][pattern]
                ps["exits"] = int(ps.get("exits") or 0) + 1
                ps["sum_pnl_usd"] = round(float(ps.get("sum_pnl_usd") or 0) + pnl, 4)
                if pnl >= 0:
                    ps["wins"] = int(ps.get("wins") or 0) + 1
                else:
                    ps["losses"] = int(ps.get("losses") or 0) + 1

    return out


def reconcile_session_stats(*, force: bool = False) -> dict[str, Any]:
    """Merge authoritative session_stats from decisions.jsonl into learned/*.json."""
    session = session_date_et()
    aggregated = aggregate_session_from_decisions(session)
    syms = list(dict.fromkeys(list(universe()) + list(aggregated.keys())))

    updated: list[dict[str, Any]] = []
    for sym in syms:
        agg = aggregated.get(sym)
        if not agg and not force:
            continue

        row: dict[str, Any] | None = None
        with _learned_lock:
            learned = load_learned(sym)
            prior = learned.get("session_stats") or {}
            prior_exits = int(prior.get("exits") or 0)

            if agg:
                new_stats = agg["session_stats"]
                new_exits = int(new_stats.get("exits") or 0)
                if not force and new_exits <= prior_exits:
                    pass
                else:
                    learned["session_stats"] = new_stats
                    learned["pattern_stats"] = agg["pattern_stats"]
                    notes = list(learned.get("notes") or [])
                    notes.append(f"{_RECONCILE_MARKER}:{session}:exits {prior_exits}->{new_exits}")
                    learned["notes"] = notes[-12:]
                    save_learned(sym, learned)
                    row = {
                        "symbol": sym,
                        "prior_exits": prior_exits,
                        "reconciled_exits": new_exits,
                        "sum_pnl_usd": float(new_stats.get("sum_pnl_usd") or 0),
                    }
            elif force:
                learned["session_stats"] = _fresh_session_stats()
                learned["pattern_stats"] = _fresh_pattern_stats()
                new_stats = learned["session_stats"]
                notes = list(learned.get("notes") or [])
                notes.append(f"{_RECONCILE_MARKER}:{session}:exits {prior_exits}->0")
                learned["notes"] = notes[-12:]
                save_learned(sym, learned)
                row = {
                    "symbol": sym,
                    "prior_exits": prior_exits,
                    "reconciled_exits": 0,
                    "sum_pnl_usd": 0.0,
                }

        if row is None:
            continue
        updated.append(row)
        try:
            refresh_adaptive_params(sym)
            catch_up_improvement(sym)
        except Exception:
            logger.exception("adaptive refresh failed for %s", sym)
        logger.info(
            "reconciled %s session_stats exits %s->%s pnl=%s",
            sym,
            row["prior_exits"],
            row["reconciled_exits"],
            row["sum_pnl_usd"],
        )

    total_pnl = round(sum(u["sum_pnl_usd"] for u in updated), 4)
    total_exits = sum(u["reconciled_exits"] for u in updated)
    report = {
        "ok": True,
        "session_date_et": session,
        "symbols_updated": len(updated),
        "total_exits": total_exits,
        "total_sum_pnl_usd": total_pnl,
        "updates": updated,
    }
    marker = swarm_data_dir() / "session_reconcile.json"
    marker.write_text(json.dumps({**report, "ts": session}, indent=2), encoding="utf-8")
    return report


def reconcile_session_on_boot() -> dict[str, Any]:
    """Called once when skim swarm agent starts."""
    try:
        return reconcile_session_stats(force=False)
    except Exception as e:
        logger.exception("session reconcile failed: %s", e)
        return {"ok": False, "error": str(e)}
