"""
Canonical system time — America/New_York only (US Eastern).

All new timestamps, cron interpretation, and operator-facing times must use this module.
Configure via FORTRESS_SYSTEM_TZ (default America/New_York). Do not use UTC for system logic.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "America/New_York"
_REGISTRY = (
    __import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "system_timezone.json"
)


def system_tz_name() -> str:
    raw = (os.environ.get("FORTRESS_SYSTEM_TZ") or "").strip()
    if raw:
        return raw
    try:
        import json

        if _REGISTRY.exists():
            doc = json.loads(_REGISTRY.read_text(encoding="utf-8"))
            reg = str(doc.get("canonical_timezone") or doc.get("iana") or "").strip()
            if reg:
                return reg
    except Exception:
        pass
    return _DEFAULT_TZ


def system_tz() -> ZoneInfo:
    return ZoneInfo(system_tz_name())


def now() -> datetime:
    """Timezone-aware now in US/New York (or FORTRESS_SYSTEM_TZ)."""
    return datetime.now(system_tz())


def now_iso() -> str:
    """ISO-8601 timestamp with US/New York offset."""
    return now().isoformat()


def parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=system_tz())
        return dt.astimezone(system_tz())
    except Exception:
        return None


def ensure_system_tz() -> None:
    """Set process TZ for subprocesses and legacy libraries (idempotent)."""
    name = system_tz_name()
    os.environ.setdefault("TZ", name)
    os.environ.setdefault("FORTRESS_SYSTEM_TZ", name)

