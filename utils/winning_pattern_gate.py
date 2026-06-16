"""Winning-pattern admission — block lifetime losers; prefer proven winners."""
from __future__ import annotations

import os
from typing import Any


def winning_pattern_gate_enabled() -> bool:
    return str(os.environ.get("FORTRESS_WINNING_PATTERN_GATE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _min_lifetime_exits() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_WINNING_PATTERN_MIN_EXITS", "3") or 3))
    except ValueError:
        return 3


def _min_negative_exp_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_WINNING_PATTERN_BLOCK_EXP_USD", "-0.02") or -0.02)
    except ValueError:
        return -0.02


def pattern_lifetime_row(pattern: str) -> dict[str, Any]:
    from utils.skim_pattern_review import swarm_lifetime_pattern_totals

    return dict((swarm_lifetime_pattern_totals() or {}).get(pattern) or {})


def pattern_lifetime_expectancy(pattern: str) -> tuple[float | None, int]:
    ps = pattern_lifetime_row(pattern)
    exits = int(ps.get("exits") or 0)
    if exits < _min_lifetime_exits():
        return None, exits
    pnl = float(ps.get("sum_pnl_usd") or 0)
    return pnl / exits, exits


def winning_pattern_entry_blocked(
    pattern: str,
    *,
    component: str = "skim_swarm",
) -> tuple[bool, str]:
    if not winning_pattern_gate_enabled() or component != "skim_swarm":
        return False, ""
    if not pattern or pattern == "?":
        return False, ""

    exp, exits = pattern_lifetime_expectancy(pattern)
    if exp is not None and exp <= _min_negative_exp_usd():
        return True, f"lifetime_pattern_negative:{pattern} exp={exp:.3f} n={exits}"

    return False, ""


def winning_pattern_score_adjustment(pattern: str) -> float:
    """Small score nudge toward lifetime winners when portfolio share is low."""
    if not winning_pattern_gate_enabled() or not pattern:
        return 0.0
    try:
        from utils.skim_pattern_review import swarm_winning_pattern_share
        from utils.skim_swarm_config import target_winning_pattern_share

        share = swarm_winning_pattern_share(min_exits=_min_lifetime_exits())
        goal = target_winning_pattern_share()
        if share is None or share >= goal:
            return 0.0
    except Exception:
        return 0.0

    exp, exits = pattern_lifetime_expectancy(pattern)
    if exp is None or exits < _min_lifetime_exits():
        return 0.0
    if exp > 0.03:
        return min(0.04, exp * 0.5)
    return 0.0
