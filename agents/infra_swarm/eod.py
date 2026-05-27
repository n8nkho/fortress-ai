"""EOD / session gates for skim swarm."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def et_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def session_date_et() -> str:
    return et_now().date().isoformat()


def is_force_flatten_window() -> bool:
    n = et_now()
    if n.weekday() >= 5:
        return True
    return n.hour * 60 + n.minute >= 15 * 60 + 50


def is_eod_caution_window() -> bool:
    n = et_now()
    if n.weekday() >= 5:
        return True
    return n.hour * 60 + n.minute >= 15 * 60 + 45


def is_opening_blackout() -> bool:
    n = et_now()
    if n.weekday() >= 5:
        return True
    return n.hour * 60 + n.minute < 9 * 60 + 45


def describe_eod_phase() -> str:
    if is_force_flatten_window():
        return "force_flatten"
    if is_eod_caution_window():
        return "eod_caution"
    if is_opening_blackout():
        return "opening_blackout"
    return "normal"
