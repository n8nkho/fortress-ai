"""Helpers for portfolio session entry_block_breakdown counters."""
from __future__ import annotations

from typing import Any


def increment_market_relative_underperformance_breakdown(
    breakdown: dict[str, Any] | None,
) -> dict[str, Any]:
    """Increment market_relative_underperformance (and legacy market_relative alias)."""
    counts = dict(breakdown or {})
    counts["market_relative_underperformance"] = (
        int(counts.get("market_relative_underperformance") or 0) + 1
    )
    counts["market_relative"] = int(counts.get("market_relative") or 0) + 1
    return counts
