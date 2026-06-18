"""Portfolio session report formatting and diagnostic logging."""
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)


def format_session_report(
    summary: dict[str, Any],
    *,
    portfolio: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
) -> str | None:
    """
    Info-level line when session has zero exits and benchmark moved up.
    Example: Entry blocks active: denylist=3, pause_entries=0, pattern_disables=5
    """
    port = portfolio or {}
    bench = benchmark or {}
    exits = int(port.get("session_exit_count") or 0)
    spy_1d = bench.get("change_1d_pct")
    if exits != 0 or spy_1d is None or float(spy_1d) <= 0:
        return None
    breakdown = summary.get("entry_block_breakdown") or {}
    return (
        "Entry blocks active: "
        f"denylist={int(breakdown.get('denylist') or 0)}, "
        f"pause_entries={int(breakdown.get('pause_entries') or 0)}, "
        f"pattern_disables={int(breakdown.get('pattern_disables') or 0)}"
    )


def log_entry_block_report(
    summary: dict[str, Any],
    *,
    portfolio: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
) -> str | None:
    """Emit format_session_report line at info when conditions match."""
    line = format_session_report(summary, portfolio=portfolio, benchmark=benchmark)
    if line:
        _LOG.info("%s", line)
    return line
