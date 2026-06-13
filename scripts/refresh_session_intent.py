#!/usr/bin/env python3
"""Generate or refresh today's session intent (09:30–10:00 ET window)."""
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

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    from utils.session_intent import ensure_session_intent

    out = ensure_session_intent(force=args.force)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") or out.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
