#!/usr/bin/env python3
"""RTH intraday autonomous SI — scan anomalies and apply fixes every 30 minutes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from utils.rth_autonomous_si import run_rth_intraday_cycle


def main() -> int:
    ap = argparse.ArgumentParser(description="Fortress RTH intraday autonomous SI cycle")
    ap.add_argument("--force", action="store_true", help="Run even outside RTH (testing)")
    ap.add_argument("--json", action="store_true", help="Print full JSON report")
    args = ap.parse_args()

    report = run_rth_intraday_cycle(force=args.force)
    if args.json or not report.get("skipped"):
        print(json.dumps(report, indent=2, default=str))
    elif report.get("skipped"):
        print(json.dumps({"skipped": report.get("skipped")}))

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
