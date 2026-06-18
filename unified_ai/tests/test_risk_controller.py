"""Unit tests for unified_ai.risk_controller."""
from __future__ import annotations

import os
import unittest


class TestRiskController(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_AI_DRY_RUN"] = "1"

    def test_flatten_legacy_positions_plans_chunked_trim(self):
        from unified_ai.risk_controller import RiskController

        positions = [{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}]
        summary = RiskController(positions, dry_run=True).flatten_legacy_positions()
        self.assertEqual(len(summary.get("flattened") or []), 1)
        rec = summary["flattened"][0]
        self.assertEqual(rec["symbol"], "IBM")
        self.assertGreater(rec["sell_qty"], 0)
        self.assertTrue(rec.get("chunked_exit"))
        self.assertGreater(len(rec.get("order_qtys") or []), 1)

    def test_skips_positions_under_cap(self):
        from unified_ai.risk_controller import RiskController

        positions = [{"sym": "IBM", "qty": 5, "mkt_value": 1000.0}]
        summary = RiskController(positions, dry_run=True).flatten_legacy_positions()
        self.assertEqual(summary.get("flattened"), [])
        self.assertEqual(len(summary.get("skipped") or []), 1)


if __name__ == "__main__":
    unittest.main()
