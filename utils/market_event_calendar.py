"""Macro event calendar — FOMC, CPI window, monthly OpEx (ET)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# FOMC decision dates 2024–2027 (announcement days, ET)
_FOMC_DATES: frozenset[str] = frozenset(
    {
        "2024-01-31",
        "2024-03-20",
        "2024-05-01",
        "2024-06-12",
        "2024-07-31",
        "2024-09-18",
        "2024-11-07",
        "2024-12-18",
        "2025-01-29",
        "2025-03-19",
        "2025-05-07",
        "2025-06-18",
        "2025-07-30",
        "2025-09-17",
        "2025-10-29",
        "2025-12-10",
        "2026-01-28",
        "2026-03-18",
        "2026-04-29",
        "2026-06-17",
        "2026-07-29",
        "2026-09-16",
        "2026-10-28",
        "2026-12-09",
        "2027-01-27",
        "2027-03-17",
    }
)


def _third_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d + timedelta(weeks=2)


def _opex_date(d: date) -> date:
    return _third_friday(d.year, d.month)


def events_for_date(d: date | None = None) -> list[dict[str, Any]]:
    """Events affecting today's session posture."""
    day = d or datetime.now(_ET).date()
    ds = day.isoformat()
    out: list[dict[str, Any]] = []
    if ds in _FOMC_DATES:
        out.append({"type": "fomc", "severity": "high", "label": "FOMC decision day"})
    if day.weekday() < 5 and 8 <= day.day <= 14:
        out.append({"type": "cpi_window", "severity": "medium", "label": "CPI release window (typical 8–14 ET)"})
    if day == _opex_date(day):
        out.append({"type": "opex", "severity": "medium", "label": "Monthly options expiration (OpEx)"})
    if day.weekday() == 0:
        out.append({"type": "monday", "severity": "low", "label": "Monday open — gap risk"})
    if day.weekday() == 4:
        out.append({"type": "friday", "severity": "low", "label": "Friday close — gamma / positioning"})
    return out


def event_summary(*, d: date | None = None) -> dict[str, Any]:
    ev = events_for_date(d)
    return {
        "session_date_et": (d or datetime.now(_ET).date()).isoformat(),
        "events": ev,
        "event_count": len(ev),
        "has_high_impact": any(e.get("severity") == "high" for e in ev),
    }
