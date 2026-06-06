#!/usr/bin/env python3
"""Mark shipped SI queue items implemented and sync deployed fix registry."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHIPPED_CODES = [
    "duplicate_entry_accumulation",
    "swarm_negative_edge_over_churn",
    "swarm_negative_edge",
    "swarm_inverted_payoff",
    "swarm_orphan_symbol_entry",
    "alpaca_bracket_tick_violation",
    "swarm_critical_pause_entries",
    "unified_off_denylist_watchlist",
    "edge_rr_cost_gates",
    "skim_winning_pattern_share_low",
    "swarm_pnl_decisions_sync",
]


def main() -> int:
    from utils.si_fix_deployment import sync_deployed_from_registry
    from utils.si_recommendation_queue import list_pending, mark_implemented_by_code, set_agent_assessment

    recorded = sync_deployed_from_registry()
    closed: dict[str, int] = {}
    for code in SHIPPED_CODES:
        items = mark_implemented_by_code(code, note="Shipped SI gap-closure 2026-06-06")
        closed[code] = len(items)

    for item in list_pending(disposition="pending_agent_review"):
        iid = str(item.get("id") or "")
        code = str(item.get("code") or "")
        if code == "skim_winning_pattern_share_low" and iid:
            set_agent_assessment(
                iid,
                worth_implementing=True,
                rationale="Auto skim_pattern_review + lifetime_pattern_stats shipped.",
                proposed_implementation="utils/skim_pattern_review.py wired to RTH SI",
            )
            mark_implemented_by_code(code, note="skim_pattern_review auto action")

    # Classic sibling mirror — close fortress_ai:* items
    tb_queue = Path("/home/ubuntu/trading-bot/data/si_recommendation_queue.json")
    sibling_closed = 0
    if tb_queue.is_file():
        doc = json.loads(tb_queue.read_text(encoding="utf-8"))
        for item in doc.get("items") or []:
            if not isinstance(item, dict) or item.get("status") != "open":
                continue
            comp = str(item.get("component") or "")
            code = str(item.get("code") or "")
            if comp.startswith("fortress_ai:") and code in SHIPPED_CODES:
                item["status"] = "implemented"
                item["disposition"] = "auto_resolved"
                item["closed_reason"] = "sibling_fix_shipped"
                sibling_closed += 1
        tb_queue.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    print(json.dumps({"deployed_synced": recorded, "closed": closed, "sibling_closed": sibling_closed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
