#!/usr/bin/env python3
"""Run continuous SI capability review (objectives vs outcomes + meta-knob updates)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)


def main() -> int:
    import argparse

    from utils.si_capability_review import run_capability_review_cycle

    ap = argparse.ArgumentParser(description="SI capability review cycle")
    ap.add_argument("--dry-run", action="store_true", help="Measure and propose only; do not apply")
    args = ap.parse_args()
    report = run_capability_review_cycle(apply=not args.dry_run)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
