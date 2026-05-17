"""RTH scheduling for SPY intraday agent."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from utils.spy_agent_config import loop_seconds_active, loop_seconds_rth, manual_only
from utils.us_equity_hours import is_us_equity_rth_et


def is_active_intraday_window_et() -> bool:
    """Core window 10:00–15:30 ET for shorter loop cadence."""
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return False
        mins = now.hour * 60 + now.minute
        return 10 * 60 <= mins < 15 * 60 + 30
    except Exception:
        return False


def effective_loop_seconds() -> float:
    if is_active_intraday_window_et():
        return loop_seconds_active()
    return loop_seconds_rth()


def should_idle(*, on_demand: bool) -> bool:
    if on_demand:
        return False
    if manual_only():
        return True
    return not is_us_equity_rth_et()
