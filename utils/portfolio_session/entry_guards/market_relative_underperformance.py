"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_THRESHOLD_PCT = -0.5


def _alpha_from_session_stats(session_stats: dict[str, Any] | Any) -> float | None:
    if isinstance(session_stats, dict):
        if not session_stats.get("benchmark_ok", True):
            return None
        for key in ("alpha_vs_spy_pct", "session_alpha_vs_spy", "alpha_vs_spy"):
            raw = session_stats.get(key)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
        session_return = session_stats.get("session_return_pct")
        if session_return is None:
            session_return = session_stats.get("session_pnl_pct")
        spy_return = session_stats.get("spy_return_pct")
        if spy_return is None:
            spy_return = session_stats.get("benchmark_change_1d_pct")
        if session_return is not None and spy_return is not None:
            try:
                return float(session_return) - float(spy_return)
            except (TypeError, ValueError):
                return None
        return None

    if isinstance(session_stats, (int, float)):
        return float(session_stats)

    if hasattr(session_stats, "benchmark_ok") and not getattr(session_stats, "benchmark_ok", True):
        return None
    for attr in ("alpha_vs_spy_pct", "session_alpha_vs_spy", "alpha_vs_spy"):
        if hasattr(session_stats, attr):
            raw = getattr(session_stats, attr)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
    return None


def _normalize_threshold(threshold: float) -> float:
    value = float(threshold)
    if value > 0:
        return -abs(value)
    return value


def check_market_relative_underperformance(
    session_stats: dict[str, Any] | Any,
    threshold: float = _DEFAULT_THRESHOLD_PCT,
) -> str | None:
    """Return block reason when alpha_vs_spy_pct underperforms threshold, else None."""
    alpha = _alpha_from_session_stats(session_stats)
    if alpha is None:
        return None

    limit = _normalize_threshold(threshold)
    if alpha >= limit:
        return None

    bps = int(round(abs(limit) * 100))
    detail = (
        f"session_underperforming alpha_vs_spy={alpha:.4f} "
        f"market_relative_underperformance_threshold={limit:.4f} "
        f"market_relative_underperformance_threshold_bps={bps}"
    )
    log.warning(
        "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative %s",
        detail,
    )
    return f"market_relative_underperformance {detail}"


__all__ = ["check_market_relative_underperformance"]
