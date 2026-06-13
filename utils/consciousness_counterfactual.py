"""Counterfactual slot expectancy — if posture holds, historical hour suggests…"""
from __future__ import annotations

from typing import Any


def slot_counterfactual_hint(
    *,
    historical_profile: dict[str, Any] | None,
    hours_remaining: float = 1.0,
) -> dict[str, Any]:
    """Compact expectancy hint from current slot profile (not a full replay)."""
    if not historical_profile:
        return {"ok": False}
    mean = float(historical_profile.get("mean_return_pct") or 0.0)
    win = float(historical_profile.get("win_rate_long") or 0.5)
    n = int(historical_profile.get("sample_count") or 0)
    if n < 8:
        return {"ok": False, "reason": "insufficient_samples"}
    projected = round(mean * max(0.5, min(6.0, hours_remaining)), 4)
    return {
        "ok": True,
        "mean_return_pct_per_hour": mean,
        "win_rate_long": win,
        "sample_count": n,
        "projected_if_hold_pct": projected,
        "hint": f"Historical slot: {mean:+.3f}%/hr (win {win*100:.0f}%, n={n}); "
        f"~{projected:+.2f}% if pattern holds over {hours_remaining:.1f}h",
    }


def hours_remaining_rth(temporal: dict[str, Any] | None) -> float:
    if not temporal or not temporal.get("rth_active"):
        return 0.0
    hour = int(temporal.get("hour_et") or 0)
    minute = int(temporal.get("minute_et") or 0)
    mins_left = (16 * 60) - (hour * 60 + minute)
    return max(0.5, mins_left / 60.0)
