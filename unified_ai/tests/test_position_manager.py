"""Unit tests for unified_ai.position_manager."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestPositionManager(unittest.TestCase):
    def test_blocks_duplicate_entry_when_already_holding(self):
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447}])
        self.assertTrue(pm.has_position("IBM"))
        gate = pm.enter_position("IBM", 10, held_qty=447)
        self.assertIsNotNone(gate)
        self.assertFalse(gate.get("allowed"))
        self.assertEqual(gate.get("block_reason"), "already_holding")
        self.assertIn("already_holding", gate.get("detail", ""))

    def test_allows_entry_when_flat(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"FORTRESS_AI_DATA_DIR": td, "POSITION_DEDUPLICATION_ENABLED": "true"},
            ):
                from unified_ai.position_manager import PositionManager

                pm = PositionManager([])
                self.assertFalse(pm.has_position("IBM"))
                gate = pm.enter_position("IBM", 10, held_qty=0)
                self.assertTrue(gate.get("allowed"))

    def test_respects_deduplication_disabled(self):
        with patch.dict(os.environ, {"POSITION_DEDUPLICATION_ENABLED": "false"}):
            from importlib import reload

            import unified_ai.settings as settings

            settings.load_defaults.cache_clear()
            reload(settings)
            from unified_ai.position_manager import PositionManager

            pm = PositionManager([{"sym": "IBM", "qty": 100}])
            gate = pm.enter_position("IBM", 5, held_qty=0)
            self.assertTrue(gate.get("allowed"))

    def test_flatten_oversized_positions_plans_chunks(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}])
        plan = pm.flatten_oversized_positions("IBM", max_notional=3000.0, px=200.0)
        self.assertFalse(plan.get("skipped"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertGreater(plan.get("sell_qty", 0), 0)
        self.assertEqual(sum(plan.get("order_qtys") or []), plan["sell_qty"])


if __name__ == "__main__":
    unittest.main()
