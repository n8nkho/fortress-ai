"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

from config.guards import market_relative_underperformance_config
from utils.portfolio_session.guards.market_relative import check_market_relative_underperformance
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard as _MarketRelativeUnderperformanceGuard,
    should_block_entry,
)
from utils.portfolio_session.risk_manager import reset_market_relative_cooldown

__all__ = [
    "MarketRelativeUnderperformanceGuard",
    "check_market_relative_underperformance",
    "reset_market_relative_guard_cooldown",
    "should_block_entry",
]


def _guard_config() -> dict:
    cfg = market_relative_underperformance_config()
    threshold = float(cfg.get("threshold_alpha_pct", -0.5))
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "threshold_alpha_pct": threshold,
        "threshold_pct": threshold,
        "underperformance_threshold_pct": threshold,
        "cooldown_seconds": int(cfg.get("cooldown_seconds") or 300),
    }


class MarketRelativeUnderperformanceGuard(_MarketRelativeUnderperformanceGuard):
    """Guard wired to ``config/guards.py`` defaults."""

    def __init__(self, *, config: dict | None = None, **kwargs) -> None:
        merged = {**_guard_config(), **(config or {}), **kwargs}
        super().__init__(config=merged)


def reset_market_relative_guard_cooldown() -> None:
    """Clear guard and risk-manager cooldown state (for tests)."""
    reset_market_relative_cooldown()
    _MarketRelativeUnderperformanceGuard.reset_cooldown()
