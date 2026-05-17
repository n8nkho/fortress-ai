"""On-demand cycle flag for SPY intraday agent (separate from unified agent)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from utils.spy_agent_config import on_demand_flag_path


def request_on_demand_cycle() -> None:
    fp = on_demand_flag_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")


def consume_on_demand_cycle() -> bool:
    fp = on_demand_flag_path()
    if not fp.exists():
        return False
    try:
        fp.unlink()
        return True
    except OSError:
        return False


def sleep_until_wake(seconds: float) -> None:
    poll = 30.0
    deadline = time.monotonic() + max(0.0, float(seconds))
    fp = on_demand_flag_path()
    while time.monotonic() < deadline:
        if fp.exists():
            return
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(poll, left))
