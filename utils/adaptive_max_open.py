"""Adaptive portfolio max-open — scale participation on strong tape, tighten via session SI."""
from __future__ import annotations

import os
from typing import Any


def adaptive_max_open_enabled() -> bool:
    return str(os.environ.get("FORTRESS_ADAPTIVE_MAX_OPEN", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def max_open_ceiling() -> int:
    """Normal adaptive ceiling (default 10)."""
    try:
        return max(1, min(20, int(os.environ.get("FORTRESS_SWARM_MAX_OPEN_CEILING", "10") or 10)))
    except ValueError:
        return 10


def max_open_aggressive_ceiling() -> int:
    """Upper bound when multiple participation signals align (default 20)."""
    try:
        return max(
            max_open_ceiling(),
            min(20, int(os.environ.get("FORTRESS_SWARM_MAX_OPEN_AGGRESSIVE", "20") or 20)),
        )
    except ValueError:
        return 20


def _config(component: str) -> Any:
    if component == "skim_swarm":
        from utils import skim_swarm_config as cfg

        return cfg
    if component == "infra_swarm":
        from utils import infra_swarm_config as cfg

        return cfg
    raise ValueError(f"unknown component: {component}")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _consciousness_bundle() -> dict[str, Any]:
    try:
        from utils.market_consciousness import assemble_consciousness_inputs

        bundle = assemble_consciousness_inputs(use_cache=True)
        return bundle if isinstance(bundle, dict) else {}
    except Exception:
        return {}


def _participation_boost_points(consciousness: dict[str, Any]) -> tuple[int, list[str]]:
    """Return (boost_points, markers) from tape + posture + session intent."""
    boost = 0
    markers: list[str] = []

    if not consciousness.get("enabled", True):
        return boost, markers

    temporal = consciousness.get("temporal") or {}
    if not temporal.get("rth_active"):
        return boost, markers

    tape = consciousness.get("market_tape") or {}
    self_st = consciousness.get("self_state") or {}
    posture = consciousness.get("consciousness_posture") or {}
    intent = consciousness.get("session_intent") or {}

    strong_tape = bool(tape.get("strong_tape_1d"))
    tape_1d = _f(tape.get("change_1d_pct"))
    alpha = _f(self_st.get("alpha_vs_spy_pct"))
    exits = int(self_st.get("session_exit_count") or 0)
    posture_mode = str(posture.get("mode") or "")

    if strong_tape:
        boost += 2
        markers.append("strong_tape_1d")

    if tape_1d >= 1.0:
        boost += 2
        markers.append(f"tape_1d={tape_1d:+.2f}%")

    if alpha < -0.25 and exits < 6:
        boost += 2
        markers.append(f"alpha_gap={alpha:+.2f}")

    if posture_mode == "participation_boost":
        boost += 3
        markers.append("participation_boost")

    if posture_mode == "defensive_tighten":
        boost -= 2
        markers.append("defensive_tighten")

    pt = _f(intent.get("participation_target"))
    if pt >= 0.55:
        boost += 1
        markers.append(f"intent_pt={pt:.2f}")

    spy_chg = _f(tape.get("spy_change_1d_pct", tape.get("change_1d_pct")))
    if spy_chg >= 0.75 and alpha < 0:
        boost += 1
        markers.append("spy_lead_underparticipation")

    return boost, markers


def compute_adaptive_max_open(component: str, *, consciousness: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Compute adaptive max-open for a swarm component.

    Returns dict with effective cap, base floor, ceiling, boost breakdown (for SI logs).
    """
    cfg = _config(component)
    base = int(cfg.max_open_positions())
    ceiling = max_open_ceiling()
    aggressive = max_open_aggressive_ceiling()

    if not adaptive_max_open_enabled():
        return {
            "enabled": False,
            "base": base,
            "ceiling": ceiling,
            "aggressive_ceiling": aggressive,
            "boost": 0,
            "effective": base,
            "markers": ["adaptive_off"],
        }

    mc = consciousness if consciousness is not None else _consciousness_bundle()
    boost, markers = _participation_boost_points(mc)

    try:
        from utils.swarm_session_si import load_session_policy

        pol = load_session_policy(component)
        exp = pol.get("session_expectancy_usd")
        wr = pol.get("session_win_rate")
        if exp is not None and float(exp) > 0:
            boost += 1
            markers.append("positive_session_exp")
        if wr is not None and float(wr) >= 0.52:
            boost += 1
            markers.append("session_wr_ok")
        if pol.get("pause_new_entries"):
            boost -= 3
            markers.append("session_pause_entries")
    except Exception:
        pass

    raw = base + boost
    if boost >= 7:
        effective = max(base, min(aggressive, raw))
    else:
        effective = max(base, min(ceiling, raw))

    return {
        "enabled": True,
        "base": base,
        "ceiling": ceiling,
        "aggressive_ceiling": aggressive,
        "boost": boost,
        "effective": max(1, effective),
        "markers": markers or ["neutral"],
    }


def adaptive_max_open_value(component: str) -> int:
    """Effective adaptive cap before session-SI tighten overlays."""
    return int(compute_adaptive_max_open(component).get("effective") or 1)
