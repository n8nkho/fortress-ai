#!/usr/bin/env python3
"""Rebuild learned session_stats from decisions.jsonl (run after restarts or manually)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from agents.skim_swarm.session_reconcile import reconcile_session_stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconcile skim learned session_stats from decisions log")
    ap.add_argument("--force", action="store_true", help="Update even when exit counts would not increase")
    args = ap.parse_args()
    report = reconcile_session_stats(force=args.force)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
