#!/usr/bin/env python3
"""Backfill data/pnl_ledger.jsonl from skim + infra decisions.jsonl exit fills."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.ai_pnl_ledger import ledger_path
from utils.swarm_decisions_pnl import iter_executed_exits
from utils.swarm_pnl_ledger import record_swarm_exit


def _existing_keys() -> set[str]:
    p = ledger_path()
    keys: set[str] = set()
    if not p.is_file():
        return keys
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        keys.add(
            "|".join(
                [
                    str(o.get("source") or ""),
                    str(o.get("symbol") or o.get("ticker") or ""),
                    str(o.get("timestamp") or "")[:19],
                    str(o.get("pnl")),
                ]
            )
        )
    return keys


def main() -> int:
    data = ROOT / "data"
    seen = _existing_keys()
    added = 0
    for component in ("skim_swarm", "infra_swarm"):
        dec = data / component / "decisions.jsonl"
        for row in iter_executed_exits(dec):
            key = "|".join(
                [
                    component,
                    row["symbol"],
                    row["ts"][:19],
                    str(row["pnl_usd"]),
                ]
            )
            if key in seen:
                continue
            record_swarm_exit(
                component,
                symbol=row["symbol"],
                pnl_usd=float(row["pnl_usd"]),
                side="SELL",
                qty=1,
                extra={"backfill": True, "session_date_et": row["session_date_et"]},
            )
            seen.add(key)
            added += 1
    print(json.dumps({"added": added, "ledger": str(ledger_path())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
