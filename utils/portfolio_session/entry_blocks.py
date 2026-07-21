"""Portfolio session entry block registry — macro blocks before swarm entries."""
from __future__ import annotations

from typing import Callable

from utils.portfolio_session.config import get_market_relative_underperformance_threshold


def market_relative_underperformance_block(
    session_alpha_vs_spy_pct: float,
    threshold: float,
) -> bool:
    """Return True (block entry) when session alpha vs SPY is below threshold."""
    return float(session_alpha_vs_spy_pct) < float(threshold)


def _market_relative_block_from_state(
    session_alpha_vs_spy_pct: float,
    threshold: float | None = None,
) -> bool:
    limit = float(
        threshold if threshold is not None else get_market_relative_underperformance_threshold()
    )
    return market_relative_underperformance_block(session_alpha_vs_spy_pct, limit)


ENTRY_BLOCK_REGISTRY: dict[str, Callable[..., bool]] = {
    "market_relative_underperformance": _market_relative_block_from_state,
    "market_relative": _market_relative_block_from_state,
}
