"""Low-severity operational alerts for Fortress monitoring."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def alert_market_relative_underperformance(
    *,
    alpha_vs_spy_pct: float,
    threshold: float,
    symbol: str = "",
) -> None:
    """Emit a low-severity alert when the market-relative underperformance guard triggers."""
    log.info(
        "alert:market_relative_underperformance severity=low symbol=%s "
        "alpha_vs_spy_pct=%.4f market_relative_underperformance_threshold=%.4f "
        "entry_blocked_by_market_relative session_underperforming",
        symbol.strip().upper() or "*",
        alpha_vs_spy_pct,
        threshold,
    )
