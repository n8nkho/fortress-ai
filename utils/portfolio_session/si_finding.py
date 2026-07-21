"""SI finding schema helpers for portfolio_session diagnostics."""
from __future__ import annotations

from typing import Any

ENTRY_BLOCK_BREAKDOWN_KEYS = (
    "denylist",
    "pause_entries",
    "pattern_disables",
    "market_relative",
)


def normalize_entry_block_breakdown(raw: dict[str, Any] | None) -> dict[str, int]:
    """Ensure entry_block_breakdown includes all known block categories."""
    src = dict(raw or {})
    return {key: int(src.get(key) or 0) for key in ENTRY_BLOCK_BREAKDOWN_KEYS}


def build_market_relative_finding_detail(
    *,
    alpha_vs_spy_pct: float | None,
    benchmark_change_1d_pct: float | None,
    session_realized_usd: float,
    session_exit_count: int,
    entry_block_breakdown: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Detail payload for market_relative_underperformance SI findings."""
    detail: dict[str, Any] = {
        "alpha_vs_spy_pct": alpha_vs_spy_pct,
        "benchmark_change_1d_pct": benchmark_change_1d_pct,
        "session_realized_usd": session_realized_usd,
        "session_exit_count": session_exit_count,
        "entry_block_breakdown": normalize_entry_block_breakdown(entry_block_breakdown),
    }
    detail.update(extra)
    return detail
