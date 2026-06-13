"""Bounded posture adjustments from market consciousness — participation + tighten-only."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

_SCORE_DELTA_MAX = 0.04
_ENTRY_DELTA_MAX = 0.05


def posture_enabled() -> bool:
    return str(os.environ.get("FORTRESS_CONSCIOUSNESS_POSTURE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def proactive_si_enabled() -> bool:
    return str(os.environ.get("FORTRESS_PROACTIVE_SI", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _strong_tape_min_exits() -> int:
    try:
        return max(1, int(os.environ.get("FORTRESS_CONSCIOUSNESS_MIN_EXITS", "4") or 4))
    except ValueError:
        return 4


def compute_consciousness_posture(
    consciousness: dict[str, Any] | None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Derive bounded score + entry-threshold deltas from consciousness bundle.
    Negative entry_threshold_delta = easier entries (participation boost).
    """
    default = {
        "enabled": False,
        "mode": "neutral",
        "score_delta": 0.0,
        "entry_threshold_delta": 0.0,
        "reasoning": "posture_off",
    }
    if not posture_enabled() or not consciousness:
        return default

    temporal = consciousness.get("temporal") or {}
    if not temporal.get("rth_active"):
        return {**default, "enabled": True, "reasoning": "outside_rth"}

    hist = consciousness.get("historical_hour_profile") or {}
    spy_prof = hist.get("SPY") or hist.get("QQQ") or {}
    mean_ret = _f(spy_prof.get("mean_return_pct"))
    win_long = _f(spy_prof.get("win_rate_long"), 0.5)

    tape = consciousness.get("market_tape") or {}
    self_st = consciousness.get("self_state") or {}
    alpha = _f(self_st.get("alpha_vs_spy_pct"))
    exits = int(self_st.get("session_exit_count") or 0)
    strong_tape = bool(tape.get("strong_tape_1d"))
    tape_1d = _f(tape.get("change_1d_pct"))

    vix = _f((features or {}).get("vix_last")) if features else 0.0
    if vix <= 0 and features:
        vix = _f(features.get("vix_last"))

    score_delta = 0.0
    entry_delta = 0.0
    mode = "neutral"
    parts: list[str] = []

    # Historical hour tilt (small directional bias)
    if mean_ret > 0.02 and win_long >= 0.52:
        score_delta += 0.012
        parts.append(f"hist_bullish_slot mean={mean_ret:+.3f}%")
    elif mean_ret < -0.03 and win_long < 0.48:
        score_delta -= 0.012
        parts.append(f"hist_bearish_slot mean={mean_ret:+.3f}%")

    # Participation gap on strong tape (Friday-style lesson)
    min_ex = _strong_tape_min_exits()
    if strong_tape and alpha < -0.25 and exits < min_ex and mean_ret >= -0.01:
        entry_delta -= min(0.045, _ENTRY_DELTA_MAX)
        score_delta += 0.018 if tape_1d >= 0 else -0.01
        mode = "participation_boost"
        parts.append(f"participation_gap alpha={alpha:+.2f} exits={exits}")

    # Defensive tighten — high VIX or historically weak hour
    if vix > 28 or mean_ret < -0.06:
        entry_delta += min(0.04, _ENTRY_DELTA_MAX)
        score_delta -= 0.015
        mode = "defensive_tighten" if mode == "neutral" else mode
        parts.append(f"defensive vix={vix:.1f}" if vix > 28 else f"weak_hour mean={mean_ret:+.3f}")

    score_delta = max(-_SCORE_DELTA_MAX, min(_SCORE_DELTA_MAX, score_delta))
    entry_delta = max(-_ENTRY_DELTA_MAX, min(_ENTRY_DELTA_MAX, entry_delta))

    return {
        "enabled": True,
        "mode": mode,
        "score_delta": round(score_delta, 4),
        "entry_threshold_delta": round(entry_delta, 4),
        "reasoning": "; ".join(parts) if parts else "neutral",
        "markers": ["consciousness_posture", mode],
    }


def consciousness_score_adjustment(posture: dict[str, Any] | None) -> float:
    if not posture or not posture.get("enabled"):
        return 0.0
    return float(posture.get("score_delta") or 0.0)


def apply_entry_threshold_delta(enter_long: float, enter_short: float, posture: dict[str, Any] | None) -> tuple[float, float]:
    """Apply posture to flat-entry thresholds only. Negative delta eases longs on boost."""
    if not posture or not posture.get("enabled"):
        return enter_long, enter_short
    delta = float(posture.get("entry_threshold_delta") or 0.0)
    if delta == 0:
        return enter_long, enter_short
    mode = str(posture.get("mode") or "")
    el, es = enter_long, enter_short
    if mode == "participation_boost":
        el = max(0.12, el + delta)
        if delta < 0:
            es = min(-0.12, es - abs(delta) * 0.5)
    elif mode == "defensive_tighten":
        el = min(0.95, el + abs(delta))
        es = max(-0.95, es - abs(delta))
    else:
        el = max(0.12, min(0.95, el + delta))
    return el, es


def enrich_features_with_consciousness_posture(
    features: dict[str, Any],
    shared: dict[str, Any],
) -> dict[str, Any]:
    mc = shared.get("market_consciousness")
    if isinstance(mc, dict):
        features["market_consciousness"] = mc
    posture = compute_consciousness_posture(mc if isinstance(mc, dict) else None, features)
    features["consciousness_posture"] = posture
    return features


def proactive_si_trigger(consciousness: dict[str, Any] | None = None) -> dict[str, Any]:
    """Detect participation gap pattern; does not run SI itself."""
    if not proactive_si_enabled():
        return {"triggered": False, "skipped": "disabled"}
    if consciousness is None:
        from utils.market_consciousness import assemble_consciousness_inputs

        consciousness = assemble_consciousness_inputs()
    if not consciousness.get("enabled"):
        return {"triggered": False, "skipped": "consciousness_off"}

    tape = consciousness.get("market_tape") or {}
    self_st = consciousness.get("self_state") or {}
    alpha = _f(self_st.get("alpha_vs_spy_pct"))
    exits = int(self_st.get("session_exit_count") or 0)
    min_ex = _strong_tape_min_exits()

    if bool(tape.get("strong_tape_1d")) and alpha < -0.3 and exits < min_ex:
        return {
            "triggered": True,
            "code": "consciousness_participation_gap",
            "alpha_vs_spy_pct": alpha,
            "session_exit_count": exits,
            "strong_tape_1d": True,
            "recommendation": "Run capability review — portfolio lagging SPY on strong tape with low participation.",
        }
    return {"triggered": False}


def _proactive_state_path() -> Path:
    root = Path(os.environ.get("FORTRESS_AI_PROJECT_ROOT") or Path(__file__).resolve().parent.parent)
    return root / "data" / "market_consciousness" / "proactive_si_last.json"


def maybe_run_proactive_si(*, force: bool = False) -> dict[str, Any]:
    """Run capability review when consciousness detects participation gap (cooldown 30m)."""
    trig = proactive_si_trigger()
    if not trig.get("triggered") and not force:
        return {"ok": True, "ran": False, "trigger": trig}

    cooldown_min = 30
    state_p = _proactive_state_path()
    now = datetime.now(_ET)
    if not force and state_p.is_file():
        try:
            st = json.loads(state_p.read_text(encoding="utf-8"))
            last = datetime.fromisoformat(str(st.get("ts")))
            if last.tzinfo is None:
                last = last.replace(tzinfo=_ET)
            if now - last.astimezone(_ET) < timedelta(minutes=cooldown_min):
                return {"ok": True, "ran": False, "skipped": "cooldown", "trigger": trig}
        except Exception:
            pass

    from utils.operator_halt import is_trading_halted

    if is_trading_halted():
        return {"ok": False, "ran": False, "skipped": "trading_halted", "trigger": trig}

    try:
        from utils.si_capability_review import run_capability_review_cycle

        cap = run_capability_review_cycle(apply=True)
    except Exception as e:
        return {"ok": False, "ran": False, "error": str(e)[:120], "trigger": trig}

    state_p.parent.mkdir(parents=True, exist_ok=True)
    state_p.write_text(
        json.dumps({"ts": now.isoformat(), "trigger": trig, "markers": ["proactive_si", "consciousness_participation_gap"]}, default=str),
        encoding="utf-8",
    )
    return {"ok": True, "ran": True, "trigger": trig, "capability_review": cap}
