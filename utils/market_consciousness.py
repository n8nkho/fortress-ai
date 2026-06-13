"""
Market consciousness — temporal memory + self-state inputs for autonomous agents.

Combines 5-year hourly slot profiles, live benchmark tape, and portfolio session
state so decisions are informed by historical hour-of-week patterns (not win-rate
objectives — expectancy/risk-adjusted posture only).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def _enabled() -> bool:
    return str(os.environ.get("FORTRESS_MARKET_CONSCIOUSNESS", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def knowledge_path() -> Path:
    from agents.historical_seeder.paths import hourly_knowledge_path

    return hourly_knowledge_path()


def load_knowledge_base() -> dict[str, Any]:
    p = knowledge_path()
    if not p.is_file():
        return {"version": 0, "slots": {}, "symbols": []}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"version": 0, "slots": {}}
    except Exception:
        return {"version": 0, "slots": {}, "symbols": []}


def current_temporal_slot(*, now: datetime | None = None) -> dict[str, Any]:
    t = now or datetime.now(_ET)
    if t.tzinfo is None:
        t = t.replace(tzinfo=_ET)
    else:
        t = t.astimezone(_ET)
    hour = t.hour
    rth = t.weekday() < 5 and ((hour > 9) or (hour == 9 and t.minute >= 30)) and hour < 16
    phase = "closed"
    if rth:
        if hour == 9:
            phase = "open"
        elif hour >= 15:
            phase = "close"
        else:
            phase = "midday"
    slot_key = f"{_WD[t.weekday()]}-{hour:02d}" if t.weekday() < 5 else None
    return {
        "session_date_et": t.strftime("%Y-%m-%d"),
        "weekday": _WD[t.weekday()],
        "hour_et": hour,
        "minute_et": t.minute,
        "rth_active": rth,
        "rth_phase": phase,
        "slot_key": slot_key,
    }


def slot_profile(kb: dict[str, Any], symbol: str, slot_key: str | None) -> dict[str, Any] | None:
    if not slot_key:
        return None
    slots = (kb.get("slots") or {}).get(symbol) or {}
    row = slots.get(slot_key)
    if not isinstance(row, dict):
        return None
    return dict(row)


def _self_state() -> dict[str, Any]:
    out: dict[str, Any] = {"halted": False}
    try:
        from utils.operator_halt import get_halt_state, is_trading_halted

        out["halted"] = bool(is_trading_halted())
        out["halt_detail"] = get_halt_state()
    except Exception:
        pass
    try:
        from utils.market_benchmark import build_portfolio_session_metrics

        port = build_portfolio_session_metrics()
        out["session_realized_usd"] = port.get("session_realized_usd")
        out["session_exit_count"] = port.get("session_exit_count")
        out["alpha_vs_spy_pct"] = port.get("alpha_vs_spy_pct")
    except Exception:
        pass
    return out


def assemble_consciousness_inputs(*, now: datetime | None = None) -> dict[str, Any]:
    """Full consciousness bundle for agents and SI (compact JSON-serializable)."""
    if not _enabled():
        return {"enabled": False}

    temporal = current_temporal_slot(now=now)
    kb = load_knowledge_base()
    slot_key = temporal.get("slot_key")
    symbols = kb.get("symbols") or ["SPY", "QQQ", "SMH"]
    historical: dict[str, Any] = {}
    for sym in symbols[:4]:
        prof = slot_profile(kb, sym, slot_key)
        if prof:
            historical[sym] = prof

    tape: dict[str, Any] = {}
    try:
        from utils.market_benchmark import fetch_benchmark_context

        tape = fetch_benchmark_context()
    except Exception as e:
        tape = {"ok": False, "error": str(e)[:80]}

    analogues: list[str] = []
    for sym, prof in historical.items():
        analogues.append(
            f"{sym}@{slot_key}: avg {prof.get('mean_return_pct'):+.3f}%/hr "
            f"(win {prof.get('win_rate_long', 0)*100:.0f}%, n={prof.get('sample_count')})"
        )

    return {
        "enabled": True,
        "temporal": temporal,
        "historical_hour_profile": historical,
        "knowledge_built_at": kb.get("built_at"),
        "knowledge_years": kb.get("years") or 5,
        "market_tape": {
            "benchmark": tape.get("benchmark"),
            "change_1d_pct": tape.get("change_1d_pct"),
            "tape_trend": tape.get("tape_trend"),
            "strong_tape_1d": tape.get("strong_tape_1d"),
        }
        if tape.get("ok")
        else {"ok": False, "error": tape.get("error")},
        "self_state": _self_state(),
        "analogue_summary": analogues,
    }


def format_consciousness_prompt_section(*, max_chars: int = 900) -> str:
    """Prompt block for LLM agents — MARKET CONSCIOUSNESS."""
    bundle = assemble_consciousness_inputs()
    if not bundle.get("enabled"):
        return ""
    if not bundle.get("historical_hour_profile") and not (bundle.get("market_tape") or {}).get("ok"):
        return ""
    compact = {
        "temporal": bundle.get("temporal"),
        "historical_hour_profile": bundle.get("historical_hour_profile"),
        "market_tape": bundle.get("market_tape"),
        "self_state": {
            k: bundle["self_state"].get(k)
            for k in ("session_realized_usd", "alpha_vs_spy_pct", "halted")
            if isinstance(bundle.get("self_state"), dict)
        },
        "analogue_summary": bundle.get("analogue_summary"),
    }
    text = "MARKET_CONSCIOUSNESS (5yr hourly memory + live tape + self-state):\n" + json.dumps(
        compact,
        separators=(",", ":"),
        default=str,
    )
    return text[:max_chars]


def attach_to_shared_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Merge compact consciousness into swarm shared context."""
    bundle = assemble_consciousness_inputs()
    if not bundle.get("enabled"):
        return ctx
    ctx = dict(ctx)
    ctx["market_consciousness"] = {
        "temporal": bundle.get("temporal"),
        "historical_hour_profile": bundle.get("historical_hour_profile"),
        "analogue_summary": bundle.get("analogue_summary"),
        "alpha_vs_spy_pct": (bundle.get("self_state") or {}).get("alpha_vs_spy_pct"),
    }
    return ctx
