"""End-of-day flatten — intraday agent must not hold overnight."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def et_now() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def session_date_et() -> str:
    return et_now().date().isoformat()


def is_eod_caution_window() -> bool:
    """After 15:45 ET — only flatten/trim allowed."""
    n = et_now()
    if n.weekday() >= 5:
        return True
    mins = n.hour * 60 + n.minute
    return mins >= 15 * 60 + 45


def is_force_flatten_window() -> bool:
    """After 15:50 ET — agent must flatten before close."""
    n = et_now()
    if n.weekday() >= 5:
        return True
    mins = n.hour * 60 + n.minute
    return mins >= 15 * 60 + 50


def is_opening_blackout() -> bool:
    """Optional: no new entries first N minutes."""
    n = et_now()
    if n.weekday() >= 5:
        return True
    mins = n.hour * 60 + n.minute
    return mins < 9 * 60 + 45


def filter_allowed_actions(actions: set[str], *, eod_caution: bool) -> set[str]:
    if not eod_caution:
        return actions
    return {a for a in actions if a in ("wait", "trim", "flatten_all")}


def describe_eod_phase() -> str:
    if is_force_flatten_window():
        return "force_flatten"
    if is_eod_caution_window():
        return "eod_caution"
    if is_opening_blackout():
        return "opening_blackout"
    return "normal"
