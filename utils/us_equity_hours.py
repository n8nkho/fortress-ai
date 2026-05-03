"""US equity regular session (ET) — used for agent loop cadence vs dashboard display."""

from __future__ import annotations

import os
from datetime import datetime


def is_us_equity_rth_et() -> bool:
    """NYSE regular session Mon–Fri 09:30–16:00 America/New_York. No holiday calendar."""
    try:
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        now = datetime.now(et)
        if now.weekday() >= 5:
            return False
        mins = now.hour * 60 + now.minute + now.second / 60.0
        open_m = 9 * 60 + 30
        close_m = 16 * 60
        return open_m <= mins < close_m
    except Exception:
        return False


def effective_loop_interval_seconds(cli_override: float | None = None) -> float:
    """RTH: FORTRESS_AI_LOOP_SECONDS; off-hours: FORTRESS_AI_LOOP_SECONDS_OFF_HOURS. Override fixes interval."""
    if cli_override is not None:
        return max(1.0, float(cli_override))
    rth = float(os.environ.get("FORTRESS_AI_LOOP_SECONDS") or "300")
    off = float(os.environ.get("FORTRESS_AI_LOOP_SECONDS_OFF_HOURS") or "1800")
    if is_us_equity_rth_et():
        return max(1.0, rth)
    return max(1.0, off)
