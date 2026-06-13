"""Episodic session memory — what the system did this RTH session, by hour slot."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def diary_dir() -> Path:
    return _root() / "data" / "market_consciousness"


def diary_path() -> Path:
    return diary_dir() / "session_diary.jsonl"


def _session_date_et() -> str:
    from agents.skim_swarm.eod import session_date_et

    return session_date_et()


def record_swarm_event(
    *,
    component: str,
    symbol: str,
    decision: dict[str, Any],
    act_result: dict[str, Any],
    features: dict[str, Any] | None = None,
) -> None:
    """Append one decision/act row for consciousness continuity."""
    if str(os.environ.get("FORTRESS_SESSION_DIARY", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    try:
        from utils.market_consciousness import current_temporal_slot

        temporal = current_temporal_slot()
    except Exception:
        temporal = {}
    alpha = None
    try:
        from utils.market_benchmark import build_portfolio_session_metrics

        alpha = (build_portfolio_session_metrics() or {}).get("alpha_vs_spy_pct")
    except Exception:
        pass
    mc = (features or {}).get("market_consciousness") if isinstance(features, dict) else None
    row = {
        "ts": datetime.now(_ET).isoformat(),
        "session_date_et": _session_date_et(),
        "slot_key": temporal.get("slot_key"),
        "component": component,
        "symbol": str(symbol or "").upper(),
        "action": decision.get("action"),
        "executed": bool(act_result.get("executed")),
        "reasoning": str(decision.get("reasoning") or "")[:120],
        "block_reason": str(act_result.get("block_reason") or "")[:80] or None,
        "alpha_vs_spy_pct": alpha,
        "score": decision.get("score"),
        "posture": ((features or {}).get("consciousness_posture") or {}).get("mode"),
    }
    if isinstance(mc, dict):
        row["tape_trend"] = (mc.get("market_tape") or {}).get("tape_trend") if "market_tape" in mc else None
    diary_dir().mkdir(parents=True, exist_ok=True)
    with open(diary_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _read_session_rows(session_date: str | None = None) -> list[dict[str, Any]]:
    sd = session_date or _session_date_et()
    p = diary_path()
    if not p.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and str(row.get("session_date_et")) == sd:
                rows.append(row)
    except Exception:
        return []
    return rows


def session_diary_summary(*, session_date: str | None = None, max_recent: int = 8) -> dict[str, Any]:
    rows = _read_session_rows(session_date)
    if not rows:
        return {"session_date_et": session_date or _session_date_et(), "events": 0}
    entries = sum(1 for r in rows if str(r.get("action") or "").startswith("enter") and r.get("executed"))
    exits = sum(
        1
        for r in rows
        if str(r.get("action") or "") in ("exit_position", "exit_partial", "flatten") and r.get("executed")
    )
    blocks = Counter(str(r.get("block_reason") or r.get("reasoning") or "unknown")[:40] for r in rows if not r.get("executed"))
    by_slot = Counter(str(r.get("slot_key") or "?") for r in rows if r.get("executed"))
    recent = rows[-max_recent:]
    return {
        "session_date_et": session_date or _session_date_et(),
        "events": len(rows),
        "entries_executed": entries,
        "exits_executed": exits,
        "top_blocks": blocks.most_common(5),
        "activity_by_slot": dict(by_slot),
        "recent": [
            {
                "slot": r.get("slot_key"),
                "symbol": r.get("symbol"),
                "action": r.get("action"),
                "executed": r.get("executed"),
                "reason": (r.get("block_reason") or r.get("reasoning") or "")[:60],
            }
            for r in recent
        ],
    }
