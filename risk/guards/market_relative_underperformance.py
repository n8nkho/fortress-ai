"""Backward-compatible re-export of market-relative underperformance guard."""
from __future__ import annotations

from risk.guards.market_relative_guard import (
    MarketRelativeGuard,
    MarketRelativeUnderperformanceGuard,
    load_market_relative_guard_config,
)

__all__ = [
    "MarketRelativeGuard",
    "MarketRelativeUnderperformanceGuard",
    "load_market_relative_guard_config",
]
