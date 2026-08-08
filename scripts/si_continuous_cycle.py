#!/usr/bin/env python3
"""Continuous SI cycle — performance → integrity → queue → auto-code (no human prompt).

Runs on a timer (and can be invoked from cron/governance). Does not require RTH.
Safety: never weakens pre_trade_gate; auto-code uses allowlist + e2e + daily caps.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)


def continuous_si_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_CONTINUOUS", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Continuous autonomous SI improvement cycle")
    ap.add_argument("--json", action="store_true", help="Print full JSON")
    ap.add_argument("--skip-code", action="store_true", help="Scan/queue only (no cursor implement)")
    args = ap.parse_args()

    if not continuous_si_enabled():
        print(json.dumps({"ok": True, "skipped": "si_continuous_disabled"}))
        return 0

    from utils.si_continuous_cycle import run_continuous_si_cycle

    out = run_continuous_si_cycle(skip_code=args.skip_code)
    print(json.dumps(out if args.json else _brief(out), indent=2, default=str))
    return 0 if out.get("ok") else 1


def _brief(out: dict) -> dict:
    ac = out.get("autonomous_code_si") or out.get("queue", {}).get("autonomous_code_si") or {}
    return {
        "ok": out.get("ok"),
        "ts": out.get("ts"),
        "marker": out.get("marker"),
        "integrity_findings": (out.get("integrity") or {}).get("findings"),
        "queue": {
            "pending_agent_review": (out.get("queue_status") or {}).get("pending_agent_review"),
            "auto_implement_queued": (out.get("queue_status") or {}).get("auto_implement_queued"),
            "pending_human_go": (out.get("queue_status") or {}).get("pending_human_go"),
        },
        "autonomous_code": {
            "assessed": ac.get("assessed"),
            "implemented": ac.get("implemented"),
            "remaining_today_cap": ac.get("remaining_today_cap"),
            "skipped": ac.get("skipped"),
            "cursor": (ac.get("cursor_cli") or {}).get("ok"),
        },
        "stale_closed": out.get("stale_closed"),
        "performance_recommendations": len(
            ((out.get("performance") or {}).get("si_effectiveness") or {}).get("recommended_fixes")
            or (out.get("performance") or {}).get("recommended_fixes")
            or []
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
