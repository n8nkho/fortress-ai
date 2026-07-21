"""Portfolio session entry guards."""
from utils.portfolio_session.guards.base import BaseGuard, GuardResult
from utils.portfolio_session.guards.market_relative import (
    MarketRelativeGuard,
    check_market_relative_underperformance,
)
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
    check_market_relative_underperformance,
    should_block_entry,
)

GUARD_REGISTRY: dict[str, type[BaseGuard]] = {
    "market_relative": MarketRelativeGuard,
    "market_relative_underperformance": MarketRelativeUnderperformanceGuard,
}

__all__ = [
    "BaseGuard",
    "GuardResult",
    "GUARD_REGISTRY",
    "MarketRelativeGuard",
    "MarketRelativeUnderperformanceGuard",
    "check_market_relative_underperformance",
    "should_block_entry",
]
