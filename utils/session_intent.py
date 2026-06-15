"""Session intent — proactive plan generated at RTH open."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def intent_path() -> Path:
    return _root() / "data" / "market_consciousness" / "session_intent.json"


def _session_date() -> str:
    from agents.skim_swarm.eod import session_date_et

    return session_date_et()


def intent_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SESSION_INTENT", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def generate_session_intent(*, consciousness: dict[str, Any] | None = None) -> dict[str, Any]:
    """Heuristic session plan from consciousness bundle — no LLM required."""
    if consciousness is None:
        from utils.market_consciousness import _assemble_consciousness_inputs_uncached

        consciousness = _assemble_consciousness_inputs_uncached()

    from utils.market_event_calendar import event_summary

    temporal = consciousness.get("temporal") or {}
    tape = consciousness.get("market_tape") or {}
    hist = consciousness.get("historical_hour_profile") or {}
    spy = hist.get("SPY") or {}
    events = event_summary()
    analogues = consciousness.get("analogue_days") or []

    participation = "moderate"
    if tape.get("strong_tape_1d") and float((consciousness.get("self_state") or {}).get("alpha_vs_spy_pct") or 0) < -0.2:
        participation = "elevated"
    elif events.get("has_high_impact") or float(spy.get("mean_return_pct") or 0) < -0.04:
        participation = "conservative"

    posture_hint = "neutral"
    if participation == "elevated":
        posture_hint = "participation_boost"
    elif participation == "conservative":
        posture_hint = "defensive_tighten"

    avoid: list[str] = []
    if temporal.get("rth_phase") == "open" or (temporal.get("hour_et") == 9):
        avoid.append("first_15m_chase")
    if events.get("has_high_impact"):
        avoid.append("pre_event_size_up")
    if temporal.get("weekday") == "Fri" and temporal.get("hour_et", 0) >= 15:
        avoid.append("late_friday_new_entries")

    priorities: list[str] = []
    if tape.get("tape_trend") == "uptrend":
        priorities.append("SMH/QQQ_residual_leaders")
    if float(spy.get("win_rate_long") or 0) >= 0.55:
        priorities.append(f"hour_slot_{temporal.get('slot_key')}_long_bias")

    plan_line = (
        f"Participation: {participation}; posture: {posture_hint}; "
        f"tape {tape.get('tape_trend') or 'unknown'} ({float(tape.get('change_1d_pct') or 0):+.2f}% 1d)"
        if tape.get("change_1d_pct") is not None
        else f"Participation: {participation}; posture: {posture_hint}"
    )

    return {
        "session_date_et": _session_date(),
        "generated_at": datetime.now(_ET).isoformat(),
        "participation_target": participation,
        "posture_hint": posture_hint,
        "plan_line": plan_line,
        "avoid": avoid,
        "priorities": priorities,
        "events": events.get("events") or [],
        "analogue_count": len(analogues),
        "markers": ["session_intent", posture_hint],
    }


def load_session_intent() -> dict[str, Any]:
    p = intent_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def save_session_intent(doc: dict[str, Any]) -> Path:
    p = intent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return p


def is_open_intent_window() -> bool:
    n = datetime.now(_ET)
    if n.weekday() >= 5:
        return False
    mins = n.hour * 60 + n.minute
    return (9 * 60 + 30) <= mins <= (10 * 60 + 0)


def ensure_session_intent(*, force: bool = False) -> dict[str, Any]:
    """Generate intent once per session (default window 09:30–10:00 ET)."""
    if not intent_enabled():
        return {"skipped": "disabled"}
    sd = _session_date()
    existing = load_session_intent()
    if not force and str(existing.get("session_date_et")) == sd and existing.get("plan_line"):
        return {"ok": True, "skipped": "fresh", "intent": existing}
    if not force and not is_open_intent_window():
        if str(existing.get("session_date_et")) == sd:
            return {"ok": True, "skipped": "outside_window", "intent": existing}
        return {"ok": False, "skipped": "outside_open_window"}
    doc = generate_session_intent()
    save_session_intent(doc)
    return {"ok": True, "generated": True, "intent": doc}
