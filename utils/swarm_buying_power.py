"""Swarm entry gates — avoid broker 40310000 when buying power is depleted."""
from __future__ import annotations

import os
from typing import Any


def min_buying_power_for_short_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_SWARM_SHORT_MIN_BUYING_POWER_USD", "150"))
    except ValueError:
        return 150.0


def short_entry_blocked(
    features: dict[str, Any] | None,
    *,
    last_price: float | None = None,
    action: str = "enter_short",
) -> tuple[bool, str]:
    """
    Block new/incremental short entries when Alpaca buying power is too low.

    Returns (blocked, reasoning_marker).
    """
    if str(os.environ.get("FORTRESS_SWARM_SHORT_BP_GATE", "1")).strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False, ""

    if action not in ("enter_short", "add_clip_short"):
        return False, ""

    feats = features if isinstance(features, dict) else {}
    try:
        bp = float(feats.get("buying_power_usd"))
    except (TypeError, ValueError):
        return False, ""

    floor = min_buying_power_for_short_usd()
    px = last_price
    if px is None:
        try:
            px = float(feats.get("last"))
        except (TypeError, ValueError):
            px = None
    if px and px > 0:
        try:
            from utils.swarm_clip_ladder import clip_size

            floor = max(floor, px * clip_size() * 1.05)
        except Exception:
            pass

    if bp < floor:
        return True, f"insufficient_buying_power_short bp={bp:.2f} need>={floor:.2f}"
    return False, ""
