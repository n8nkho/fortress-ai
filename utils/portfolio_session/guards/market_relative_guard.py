"""Backward-compatible re-exports for market-relative entry guards."""
from __future__ import annotations

from utils.portfolio_session.guards.market_relative import MarketRelativeGuard
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
    check_market_relative_underperformance,
    should_block_entry,
)

__all__ = [
    "MarketRelativeGuard",
    "MarketRelativeUnderperformanceGuard",
    "check_market_relative_underperformance",
    "should_block_entry",
]
