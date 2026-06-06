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
    from utils.integrity_diagnostics import run_integrity_scan
    from utils.si_recommendation_queue import process_scan_to_queue, status_dict

    out: dict = {}
    scan = run_integrity_scan(log=False)
    out["integrity_scan"] = {"counts": scan.get("counts"), "findings": len(scan.get("findings") or [])}
    out["recommendation_queue"] = process_scan_to_queue(scan)
    out["queue_status"] = status_dict()
    gov = get_engine().process_autonomous_governance()
    if gov:
        out["governance"] = gov
    out["reversions"] = PerformanceMonitor().monitor_active_changes()
    try:
        from utils.si_capability_review import run_capability_review_cycle

        out["capability_review"] = run_capability_review_cycle(apply=True)
    except Exception as e:
        out["capability_review"] = {"error": str(e)[:120]}
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
