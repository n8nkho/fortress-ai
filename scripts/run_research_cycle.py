#!/usr/bin/env python3
"""Nightly research cycle — promote/kill hypotheses from replay + adversarial stress."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from utils.research_cycle import run_research_cycle


def main() -> int:
    ap = argparse.ArgumentParser(description="Fortress research cycle (hypothesis promote/kill)")
    ap.add_argument("--component", default="skim_swarm", help="skim_swarm (default)")
    ap.add_argument("--sessions", type=int, default=5, help="Recent ET sessions to analyze")
    ap.add_argument("--apply", action="store_true", help="Apply promoted scenario_stress overlays")
    ap.add_argument("--json", action="store_true", help="Print full JSON report")
    args = ap.parse_args()

    report = run_research_cycle(
        component=args.component,
        max_sessions=max(1, args.sessions),
        apply_promoted=args.apply,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(json.dumps(report, indent=2))
        if report.get("newly_promoted"):
            print("newly_promoted:", ",".join(report["newly_promoted"]))

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
