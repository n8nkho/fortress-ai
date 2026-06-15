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
_VIX_LOW = 18.0
_VIX_HIGH = 25.0
_ASSEMBLE_GUARD = False
_CONSCIOUSNESS_CACHE: dict[str, Any] = {"ts": 0.0, "bundle": {}}
_CONSCIOUSNESS_CACHE_TTL = float(os.environ.get("FORTRESS_CONSCIOUSNESS_CACHE_SEC", "45") or 45)


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


def vix_regime(vix: float | None) -> str | None:
    if vix is None or vix <= 0:
        return None
    if vix < _VIX_LOW:
        return "low"
    if vix <= _VIX_HIGH:
        return "mid"
    return "high"


def slot_profile(
    kb: dict[str, Any],
    symbol: str,
    slot_key: str | None,
    *,
    vix: float | None = None,
) -> dict[str, Any] | None:
    if not slot_key:
        return None
    reg = vix_regime(vix)
    if reg:
        reg_slots = ((kb.get("slots_regime") or {}).get(symbol) or {}).get(reg) or {}
        row = reg_slots.get(slot_key)
        if isinstance(row, dict) and int(row.get("sample_count") or 0) >= 8:
            return dict(row)
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


def assemble_consciousness_inputs(*, now: datetime | None = None, use_cache: bool = True) -> dict[str, Any]:
    """Full consciousness bundle for agents and SI (compact JSON-serializable)."""
    global _ASSEMBLE_GUARD
    if not _enabled():
        return {"enabled": False}

    if use_cache and _CONSCIOUSNESS_CACHE.get("bundle"):
        import time

        age = time.time() - float(_CONSCIOUSNESS_CACHE.get("ts") or 0)
        if age < _CONSCIOUSNESS_CACHE_TTL:
            return dict(_CONSCIOUSNESS_CACHE["bundle"])

    if _ASSEMBLE_GUARD:
        return {"enabled": True, "recursive_guard": True, "temporal": current_temporal_slot(now=now)}

    _ASSEMBLE_GUARD = True
    try:
        bundle = _assemble_consciousness_inputs_uncached(now=now)
    finally:
        _ASSEMBLE_GUARD = False

    if bundle.get("enabled"):
        import time

        _CONSCIOUSNESS_CACHE["ts"] = time.time()
        _CONSCIOUSNESS_CACHE["bundle"] = bundle
    return bundle


def _assemble_consciousness_inputs_uncached(*, now: datetime | None = None) -> dict[str, Any]:
    temporal = current_temporal_slot(now=now)
    kb = load_knowledge_base()
    slot_key = temporal.get("slot_key")
    symbols = kb.get("symbols") or ["SPY", "QQQ", "SMH"]
    vix_live: float | None = None
    try:
        import yfinance as yf

        vix_live = float(yf.Ticker("^VIX").fast_info.get("last_price") or 0) or None
    except Exception:
        pass
    historical: dict[str, Any] = {}
    for sym in symbols[:4]:
        prof = slot_profile(kb, sym, slot_key, vix=vix_live)
        if prof:
            prof = dict(prof)
            if vix_live and vix_regime(vix_live):
                prof["vix_regime"] = vix_regime(vix_live)
            historical[sym] = prof

    tape: dict[str, Any] = {}
    try:
        from utils.market_benchmark import fetch_benchmark_context

        tape = fetch_benchmark_context()
    except Exception as e:
        tape = {"ok": False, "error": str(e)[:80]}

    analogues: list[str] = []
    for sym, prof in historical.items():
        reg = prof.get("vix_regime")
        reg_tag = f" vix={reg}" if reg else ""
        analogues.append(
            f"{sym}@{slot_key}{reg_tag}: avg {prof.get('mean_return_pct'):+.3f}%/hr "
            f"(win {prof.get('win_rate_long', 0)*100:.0f}%, n={prof.get('sample_count')})"
        )

    diary: dict[str, Any] = {}
    try:
        from utils.session_diary import session_diary_summary

        diary = session_diary_summary()
    except Exception:
        pass

    tape_live = tape if tape.get("ok") else {}
    if vix_live:
        tape_live = {**tape_live, "vix_last": vix_live}

    analogue_days: list[dict[str, Any]] = []
    analogue_text = ""
    try:
        from utils.analogue_days import analogue_summary, find_analogue_days

        analogue_days = find_analogue_days(k=5, live_tape=tape_live)
        analogue_text = analogue_summary(analogue_days)
    except Exception:
        pass

    events: dict[str, Any] = {}
    try:
        from utils.market_event_calendar import event_summary

        events = event_summary()
    except Exception:
        pass

    counterfactual: dict[str, Any] = {}
    try:
        from utils.consciousness_counterfactual import hours_remaining_rth, slot_counterfactual_hint

        counterfactual = slot_counterfactual_hint(
            historical_profile=historical.get("SPY"),
            hours_remaining=hours_remaining_rth(temporal),
        )
    except Exception:
        pass

    session_intent: dict[str, Any] = {}
    try:
        from utils.session_intent import load_session_intent

        session_intent = load_session_intent()
    except Exception:
        pass

    posture: dict[str, Any] = {}
    try:
        from utils.consciousness_posture import compute_consciousness_posture

        partial = {
            "temporal": temporal,
            "historical_hour_profile": historical,
            "market_tape": tape_live if tape.get("ok") else tape,
            "self_state": _self_state(),
        }
        posture = compute_consciousness_posture(partial, {"vix_last": vix_live})
    except Exception:
        pass

    return {
        "enabled": True,
        "temporal": temporal,
        "historical_hour_profile": historical,
        "session_diary": diary,
        "session_intent": session_intent,
        "analogue_days": analogue_days,
        "analogue_day_summary": analogue_text,
        "market_events": events,
        "counterfactual_hint": counterfactual,
        "consciousness_posture": posture,
        "knowledge_built_at": kb.get("built_at"),
        "knowledge_years": kb.get("years") or 5,
        "vix_regime": vix_regime(vix_live),
        "market_tape": {
            "benchmark": tape.get("benchmark"),
            "change_1d_pct": tape.get("change_1d_pct"),
            "change_5d_pct": tape.get("change_5d_pct"),
            "tape_trend": tape.get("tape_trend"),
            "strong_tape_1d": tape.get("strong_tape_1d"),
            "vix_last": vix_live,
        }
        if tape.get("ok")
        else {"ok": False, "error": tape.get("error"), "vix_last": vix_live},
        "self_state": _self_state(),
        "analogue_summary": analogues,
    }


def format_consciousness_prompt_section(*, max_chars: int = 1200) -> str:
    """Prompt block for LLM agents — MARKET CONSCIOUSNESS."""
    bundle = assemble_consciousness_inputs()
    if not bundle.get("enabled"):
        return ""
    if not bundle.get("historical_hour_profile") and not (bundle.get("market_tape") or {}).get("ok"):
        return ""
    compact = {
        "temporal": bundle.get("temporal"),
        "session_intent": {
            k: (bundle.get("session_intent") or {}).get(k)
            for k in ("plan_line", "participation_target", "posture_hint", "avoid", "priorities")
        },
        "historical_hour_profile": bundle.get("historical_hour_profile"),
        "market_tape": bundle.get("market_tape"),
        "self_state": {
            k: bundle["self_state"].get(k)
            for k in ("session_realized_usd", "alpha_vs_spy_pct", "halted")
            if isinstance(bundle.get("self_state"), dict)
        },
        "analogue_day_summary": bundle.get("analogue_day_summary"),
        "analogue_summary": bundle.get("analogue_summary"),
        "counterfactual_hint": (bundle.get("counterfactual_hint") or {}).get("hint"),
        "market_events": (bundle.get("market_events") or {}).get("events"),
        "session_diary": {
            k: bundle.get("session_diary", {}).get(k)
            for k in ("entries_executed", "exits_executed", "activity_by_slot", "recent")
            if isinstance(bundle.get("session_diary"), dict)
        },
    }
    text = "MARKET_CONSCIOUSNESS (memory + intent + analogues + self-state):\n" + json.dumps(
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
        "analogue_day_summary": bundle.get("analogue_day_summary"),
        "session_diary": bundle.get("session_diary"),
        "session_intent": bundle.get("session_intent"),
        "market_events": bundle.get("market_events"),
        "counterfactual_hint": bundle.get("counterfactual_hint"),
        "consciousness_posture": bundle.get("consciousness_posture"),
        "alpha_vs_spy_pct": (bundle.get("self_state") or {}).get("alpha_vs_spy_pct"),
        "market_tape": bundle.get("market_tape"),
    }
    return ctx


def consciousness_dashboard_snapshot() -> dict[str, Any]:
    """Full bundle for dashboard API."""
    bundle = assemble_consciousness_inputs()
    kb_age_days: float | None = None
    try:
        from utils.system_time import now, parse_iso

        built = parse_iso(str(bundle.get("knowledge_built_at") or ""))
        if built is not None:
            kb_age_days = round((now() - built).total_seconds() / 86400.0, 1)
    except Exception:
        pass
    beliefs: list[dict[str, Any]] = []
    try:
        from utils.belief_manager import get_beliefs_for_consciousness

        beliefs = get_beliefs_for_consciousness(bundle, limit=5)
    except Exception:
        pass
    proactive: dict[str, Any] = {}
    try:
        from utils.consciousness_posture import proactive_si_trigger

        proactive = proactive_si_trigger(bundle)
    except Exception:
        pass
    return {
        **bundle,
        "kb_age_days": kb_age_days,
        "matched_beliefs": beliefs,
        "proactive_si_trigger": proactive,
    }
