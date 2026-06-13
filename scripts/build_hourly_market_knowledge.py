#!/usr/bin/env python3
"""Build 5-year hourly market knowledge base for consciousness inputs."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download hourly bars and build slot knowledge JSON")
    ap.add_argument("--skip-download", action="store_true", help="Rebuild knowledge from existing CSVs only")
    ap.add_argument("--force-download", action="store_true", help="Re-fetch hourly CSVs even if fresh")
    ap.add_argument("--years", type=int, default=None, help="Override FORTRESS_HOURLY_KNOWLEDGE_YEARS")
    args = ap.parse_args(argv)

    if args.years is not None:
        import os

        os.environ["FORTRESS_HOURLY_KNOWLEDGE_YEARS"] = str(args.years)

    from agents.historical_seeder.hourly_knowledge import run_build

    out = run_build(download=not args.skip_download, force_download=args.force_download)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("symbols") else 1


if __name__ == "__main__":
    sys.exit(main())
