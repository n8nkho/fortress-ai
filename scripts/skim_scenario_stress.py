#!/usr/bin/env python3
"""Stress-test skim params against recent session replays from decisions.jsonl."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from agents.skim_swarm.scenario_stress import apply_scenario_stress_to_learned, stress_universe
from utils.skim_swarm_config import swarm_data_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="Skim scenario stress from recent decisions.jsonl")
    ap.add_argument("--sessions", type=int, default=5, help="Recent ET sessions to replay (default 5)")
    ap.add_argument("--apply", action="store_true", help="Merge recommended overlays into learned/*.json")
    ap.add_argument("--json", action="store_true", help="Print full JSON report")
    args = ap.parse_args()

    out = swarm_data_dir() / "scenario_stress_report.json"
    report = stress_universe(max_sessions=max(1, args.sessions), out_path=out)
    applied = apply_scenario_stress_to_learned(report) if args.apply else []

    if args.json:
        print(json.dumps({"report": report, "applied": applied}, indent=2))
    else:
        print(json.dumps(report, indent=2))
        if applied:
            print("applied_to:", ",".join(applied))
        rec = [r for r in report.get("symbols") or [] if r.get("apply_recommended")]
        if rec:
            print("apply_recommended:", ",".join(r["symbol"] for r in rec))

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
