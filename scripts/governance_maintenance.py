#!/usr/bin/env python3
"""Autonomous governance cron — veto windows + performance monitor (no human step)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)


def main() -> int:
    from agents.self_improvement_engine import get_engine
    from agents.performance_monitor import PerformanceMonitor

    out: dict = {}
    gov = get_engine().process_autonomous_governance()
    if gov:
        out["governance"] = gov
    out["reversions"] = PerformanceMonitor().monitor_active_changes()
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
