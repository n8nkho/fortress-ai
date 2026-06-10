#!/usr/bin/env python3
"""Run autonomous code SI cycle (assess pending → implement queued)."""
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

    p = argparse.ArgumentParser(description="Autonomous SI code implementation")
    p.add_argument("--assess-limit", type=int, default=5)
    p.add_argument("--implement-limit", type=int, default=1)
    p.add_argument("--dry-run-item", metavar="ID", help="Build prompt only for queue item")
    args = p.parse_args()

    if args.dry_run_item:
        from utils.si_code_implementation import implement_item

        print(json.dumps(implement_item(args.dry_run_item, dry_run=True), indent=2))
        return 0

    from utils.si_code_implementation import run_autonomous_code_si_cycle

    out = run_autonomous_code_si_cycle(
        assess_limit=args.assess_limit,
        implement_limit=args.implement_limit,
    )
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
