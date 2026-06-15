#!/usr/bin/env python3
"""Write operator status snapshot (services, swarms, SI, auto-code)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fortress operator status report")
    ap.add_argument("--markdown", action="store_true", help="Print markdown summary")
    ap.add_argument("--json", action="store_true", help="Print full JSON")
    args = ap.parse_args()

    from utils.operator_status_report import format_operator_status_markdown, persist_operator_status

    doc = persist_operator_status()
    if args.json:
        import json

        print(json.dumps(doc, indent=2, default=str))
    else:
        print(format_operator_status_markdown(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
