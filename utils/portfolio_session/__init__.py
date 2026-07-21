"""Portfolio session — entry block tracking and session reporting."""
from __future__ import annotations

from utils.portfolio_session.config import (
    MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD,
    get_market_relative_underperformance_threshold,
)
from utils.portfolio_session.entry_manager import EntryManager, get_entry_manager
from utils.portfolio_session.reporting import format_session_report, log_entry_block_report
from utils.portfolio_session.session_summary import generate_summary

__all__ = [
    "EntryManager",
    "MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD",
    "format_session_report",
    "generate_summary",
    "get_entry_manager",
    "get_market_relative_underperformance_threshold",
    "log_entry_block_report",
]
