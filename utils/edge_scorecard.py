"""Session edge scorecard — payoff, profit factor, expectancy by symbol/pattern."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.edge_quality import payoff_ratio, profit_factor, session_expectancy


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def scorecard_path(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name / "edge_scorecard.json"


def _session_et(ts: str) -> str | None:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (t - timedelta(hours=4)).strftime("%Y-%m-%d")
    except Exception:
        return None


def compute_scorecard_from_decisions(
    decisions_path: Path,
    *,
    session_date: str | None = None,
) -> dict[str, Any]:
    pending: dict[str, dict[str, Any]] = {}
    pnls: list[float] = []
    by_sym: dict[str, list[float]] = defaultdict(list)
    by_pat: dict[str, list[float]] = defaultdict(list)
    by_exit: dict[str, list[float]] = defaultdict(list)

    if not decisions_path.exists():
        return {"ok": False, "error": "no_decisions"}

    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            wave = json.loads(line)
        except json.JSONDecodeError:
            continue
        sess = _session_et(str(wave.get("ts") or ""))
        if session_date and sess != session_date:
            continue
        if not session_date and sess:
            session_date = sess

        for row in wave.get("results") or []:
            sym = str(row.get("symbol") or "").upper()
            dec = row.get("decision") or {}
            act = row.get("act") or {}
            feats = row.get("features") or {}
            if not act.get("executed"):
                continue
            action = str(dec.get("action") or "").lower()
            if action in ("enter_long", "enter_short", "add_clip_long", "add_clip_short"):
                pending[sym] = {"pattern": _pattern_from_reason(str(dec.get("reasoning") or ""))}
                continue
            if action not in ("exit_position", "exit_partial", "flatten"):
                continue
            pnl_raw = feats.get("unrealized_usd")
            if pnl_raw is None:
                continue
            pnl = float(pnl_raw)
            if action == "exit_partial":
                eq = max(1, int(dec.get("exit_qty") or 1))
                pq = max(1, int(feats.get("position_qty") or eq))
                pnl = pnl / pq * eq
            pnls.append(pnl)
            by_sym[sym].append(pnl)
            pat = pending.pop(sym, {}).get("pattern") or "?"
            by_pat[pat].append(pnl)
            er = _exit_reason(str(dec.get("reasoning") or ""))
            by_exit[er].append(pnl)

    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    pf = profit_factor(sum(wins), sum(losses))
    pay = payoff_ratio(aw, al)

    def _summ(rows: list[float]) -> dict[str, Any]:
        if not rows:
            return {"exits": 0}
        w = [x for x in rows if x >= 0]
        l = [x for x in rows if x < 0]
        aw2 = sum(w) / len(w) if w else 0.0
        al2 = sum(l) / len(l) if l else 0.0
        return {
            "exits": len(rows),
            "pnl_usd": round(sum(rows), 4),
            "win_rate": round(len(w) / len(rows), 4),
            "expectancy_usd": round(sum(rows) / len(rows), 4),
            "payoff_ratio": round(payoff_ratio(aw2, al2) or 0, 3),
            "profit_factor": round(profit_factor(sum(w), sum(l)) or 0, 3),
        }

    return {
        "ok": True,
        "session_date": session_date,
        "ts": datetime.now(timezone.utc).isoformat(),
        "exits": len(pnls),
        "sum_pnl_usd": round(sum(pnls), 4),
        "expectancy_usd": session_expectancy(sum(pnls), len(pnls)),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "avg_win_usd": round(aw, 4),
        "avg_loss_usd": round(al, 4),
        "payoff_ratio": round(pay, 3) if pay else None,
        "profit_factor": round(pf, 3) if pf else None,
        "breakeven_payoff_needed": round((1 - len(wins) / len(pnls)) / (len(wins) / len(pnls)), 3)
        if pnls and wins
        else None,
        "by_symbol": {k: _summ(v) for k, v in sorted(by_sym.items())},
        "by_pattern": {k: _summ(v) for k, v in sorted(by_pat.items())},
        "by_exit_reason": {k: _summ(v) for k, v in sorted(by_exit.items())},
    }


def save_scorecard(component: str, doc: dict[str, Any]) -> None:
    p = scorecard_path(component)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def load_scorecard(component: str) -> dict[str, Any]:
    p = scorecard_path(component)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


_PATTERNS = (
    "rip_fade",
    "pullback_uptrend",
    "momentum_long",
    "momentum_short",
    "layer_catch_up_long",
    "layer_catch_up_short",
    "layer_rip_fade",
    "equipment_capex_confirm",
    "power_parity",
    "stack_momentum_long",
)


def _pattern_from_reason(reason: str) -> str:
    for p in _PATTERNS:
        if p in reason:
            return p
    return "?"


def _exit_reason(reason: str) -> str:
    if "target" in reason or reason.startswith("skim_target") or reason.startswith("infra_target"):
        return "target"
    if reason.startswith("stop_loss"):
        return "stop"
    if "trailing" in reason:
        return "trailing"
    if "time_stop" in reason:
        return "time_stop"
    if "flatten" in reason:
        return "eod_flatten"
    return "other"
