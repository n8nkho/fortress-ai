#!/usr/bin/env python3
"""Run per-symbol historical skim strategy verification and optional param seeding."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from agents.skim_swarm.historical_verify import apply_recommendations_to_learned, verify_universe
from utils.skim_swarm_config import swarm_data_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Skim per-symbol historical verification")
    ap.add_argument("--years", type=int, default=10, help="Lookback years (default 10)")
    ap.add_argument("--apply", action="store_true", help="Merge recommendations into learned/*.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out = swarm_data_dir() / "historical_verify_report.json"
    report = verify_universe(years=args.years, out_path=out)
    applied = apply_recommendations_to_learned(report) if args.apply and report.get("ok") else []

    if args.json:
        print(json.dumps({"report": report, "applied": applied}, indent=2))
    else:
        print(json.dumps(report, indent=2))
        if applied:
            print("applied_to:", ",".join(applied))

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
