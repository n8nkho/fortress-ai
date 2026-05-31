"""Edge-quality feature flags — shared by skim and infra swarms."""
from __future__ import annotations

import os


def _flag_on(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def edge_quality_enabled() -> bool:
    return _flag_on("FORTRESS_EDGE_QUALITY", "1")


def rr_gate_enabled() -> bool:
    if not edge_quality_enabled():
        return False
    return _flag_on("FORTRESS_RR_GATE", "1")


def cost_gate_enabled() -> bool:
    if not edge_quality_enabled():
        return False
    return _flag_on("FORTRESS_COST_GATE", "1")


def expectancy_gate_enabled() -> bool:
    if not edge_quality_enabled():
        return False
    return _flag_on("FORTRESS_EXPECTANCY_GATE", "1")


def bracket_exits_enabled() -> bool:
    if not edge_quality_enabled():
        return False
    return _flag_on("FORTRESS_BRACKET_EXITS", "1")


def passive_entry_enabled() -> bool:
    if not edge_quality_enabled():
        return False
    return _flag_on("FORTRESS_PASSIVE_ENTRY", "1")


def time_stop_enabled() -> bool:
    if not edge_quality_enabled():
        return False
    return _flag_on("FORTRESS_TIME_STOP", "1")


def rr_safety_margin() -> float:
    try:
        return max(1.0, float(os.environ.get("FORTRESS_RR_SAFETY_MARGIN", "1.15") or 1.15))
    except ValueError:
        return 1.15


def cost_gate_mult() -> float:
    """Target must exceed round-trip cost × this multiplier."""
    try:
        return max(1.5, float(os.environ.get("FORTRESS_COST_GATE_MULT", "2.5") or 2.5))
    except ValueError:
        return 2.5


def est_slippage_usd() -> float:
    try:
        return max(0.0, float(os.environ.get("FORTRESS_EST_SLIPPAGE_USD", "0.02") or 0.02))
    except ValueError:
        return 0.02


def est_fee_usd() -> float:
    try:
        return max(0.0, float(os.environ.get("FORTRESS_EST_FEE_USD", "0.0") or 0.0))
    except ValueError:
        return 0.0


def time_stop_sec() -> float:
    try:
        return max(30.0, float(os.environ.get("FORTRESS_TIME_STOP_SEC", "120") or 120))
    except ValueError:
        return 120.0


def time_stop_min_progress_pct() -> float:
    """Exit if unrealized < target × this after time_stop_sec."""
    try:
        return max(0.05, float(os.environ.get("FORTRESS_TIME_STOP_MIN_PROGRESS", "0.20") or 0.20))
    except ValueError:
        return 0.20


def expectancy_min_exits() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_EXPECTANCY_MIN_EXITS", "4") or 4))
    except ValueError:
        return 4


def expectancy_min_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_EXPECTANCY_MIN_USD", "-0.01") or -0.01)
    except ValueError:
        return -0.01
