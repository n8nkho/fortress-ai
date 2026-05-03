"""Dashboard-controlled agent behavior outside US equity RTH (file-backed, shared with systemd agent)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def _data_root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parent.parent / "data"


def prefs_path() -> Path:
    p = _data_root()
    p.mkdir(parents=True, exist_ok=True)
    return p / "agent_runtime.json"


def on_demand_flag_path() -> Path:
    return _data_root() / "agent_on_demand_cycle.flag"


# Default False: outside Mon–Fri 9:30–16:00 America/New_York, agent idles until you enable
# the dashboard toggle or use "Run AI cycle now".
DEFAULT_RUN_OFF_HOURS_AUTO = False


def read_runtime_prefs() -> dict[str, bool | str | None]:
    path = prefs_path()
    if not path.exists():
        return {"run_off_hours_auto": DEFAULT_RUN_OFF_HOURS_AUTO}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return {
            "run_off_hours_auto": bool(d.get("run_off_hours_auto", DEFAULT_RUN_OFF_HOURS_AUTO)),
            "updated_at_utc": d.get("updated_at_utc"),
        }
    except Exception:
        return {"run_off_hours_auto": DEFAULT_RUN_OFF_HOURS_AUTO}


def write_runtime_prefs(*, run_off_hours_auto: bool) -> dict[str, str | bool]:
    path = prefs_path()
    rec = {
        "run_off_hours_auto": bool(run_off_hours_auto),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def get_run_off_hours_auto() -> bool:
    return bool(read_runtime_prefs().get("run_off_hours_auto", DEFAULT_RUN_OFF_HOURS_AUTO))


def request_on_demand_cycle() -> None:
    on_demand_flag_path().write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")


def consume_on_demand_cycle() -> bool:
    fp = on_demand_flag_path()
    if not fp.exists():
        return False
    try:
        fp.unlink()
        return True
    except OSError:
        return False


def off_hours_poll_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("FORTRESS_AI_OFF_HOURS_POLL_SECONDS") or "60"))
    except ValueError:
        return 60.0


def sleep_until_next_cycle_or_wake(seconds: float) -> None:
    """Sleep up to `seconds`, waking early if an on-demand cycle flag appears (checked every poll chunk)."""
    poll = min(off_hours_poll_seconds(), 60.0)
    deadline = time.monotonic() + max(0.0, float(seconds))
    fp = on_demand_flag_path()
    while time.monotonic() < deadline:
        if fp.exists():
            return
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(poll, left))
