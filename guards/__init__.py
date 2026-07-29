"""Fortress entry guard registry."""
from __future__ import annotations

from guards.market_relative_guard import (
    MarketRelativeUnderperformanceGuard,
    check_market_relative_underperformance,
    reset_market_relative_guard_cooldown,
    should_block_entry,
)
from utils.portfolio_session.guards.base import BaseGuard, GuardResult

GUARD_REGISTRY: dict[str, type[BaseGuard]] = {
    "market_relative_underperformance": MarketRelativeUnderperformanceGuard,
}

__all__ = [
    "BaseGuard",
    "GUARD_REGISTRY",
    "GuardResult",
    "MarketRelativeUnderperformanceGuard",
    "check_market_relative_underperformance",
    "reset_market_relative_guard_cooldown",
    "should_block_entry",
]
