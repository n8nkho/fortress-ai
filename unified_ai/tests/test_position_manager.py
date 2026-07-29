"""Unit tests for unified_ai.position_manager."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestPositionManager(unittest.TestCase):
    def test_can_enter_blocks_duplicate(self):
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447}])
        allowed, reason = pm.can_enter("IBM", held_qty=447)
        self.assertFalse(allowed)
        self.assertIn("already_holding", reason)

    def test_blocks_duplicate_entry_when_already_holding(self):
        from unified_ai.position_manager import PositionDeduplicationError, PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447}])
        self.assertTrue(pm.has_position("IBM"))
        with self.assertRaises(PositionDeduplicationError) as ctx:
            pm.enter_position("IBM", 10, held_qty=447)
        self.assertIn("already_holding", ctx.exception.detail)
        allowed, reason = pm.can_enter("IBM", held_qty=447)
        self.assertFalse(allowed)
        self.assertIn("already_holding", reason)

    def test_blocks_pending_entry_symbol(self):
        from unified_ai.position_manager import PositionError, PositionManager

        pm = PositionManager([], pending_entries={"IBM"})
        gate = pm.enter_position("IBM", 10, held_qty=0)
        self.assertFalse(gate.get("allowed"))
        self.assertEqual(gate.get("block_reason"), "already_holding")
        self.assertEqual(gate.get("error"), PositionError.DUPLICATE_ENTRY.value)

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

    def test_get_oversized_positions_in_position_manager(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_MAX_POSITION_NOTIONAL_USD"] = "3000"
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}])
        oversized = pm.get_oversized_positions(3000.0)
        self.assertEqual(len(oversized), 1)
        self.assertEqual(oversized[0]["sym"], "IBM")

    def test_flatten_oversized_positions_plans_chunks(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_MAX_POSITION_NOTIONAL_USD"] = "3000"
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}])
        plan = pm.flatten_oversized_positions("IBM", max_notional=3000.0, px=200.0)
        self.assertFalse(plan.get("skipped"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertGreater(plan.get("sell_qty", 0), 0)
        self.assertEqual(sum(plan.get("order_qtys") or []), plan["sell_qty"])

    def test_flatten_legacy_position_alias(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_MAX_POSITION_NOTIONAL_USD"] = "3000"
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}])
        plan = pm.flatten_legacy_position("IBM", 3000.0, px=200.0)
        self.assertFalse(plan.get("skipped"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertEqual(sum(plan.get("order_qtys") or []), plan["sell_qty"])

    def test_flatten_all_oversized_positions(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_MAX_POSITION_NOTIONAL_USD"] = "3000"
        from unified_ai.position_manager import PositionManager

        pm = PositionManager(
            [
                {"sym": "IBM", "qty": 447, "mkt_value": 89400.0},
                {"sym": "AAPL", "qty": 5, "mkt_value": 1000.0},
            ]
        )
        summary = pm.flatten_all_oversized_positions(max_notional=3000.0, equity=100_000.0)
        self.assertEqual(len(summary.get("flattened") or []), 1)
        self.assertEqual(len(summary.get("skipped") or []), 1)

    def test_exit_position_respects_max_chunk_size(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "50000"
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447}])
        plan = pm.exit_position("IBM", 447, px=200.0, max_chunk_size=3000.0)
        self.assertTrue(plan.get("chunked_exit"))
        self.assertEqual(sum(plan["order_qtys"]), 447)

    def test_exit_chunking_via_order_executor(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        from unified_ai.order_executor import OrderExecutor

        ex = OrderExecutor([{"sym": "IBM", "qty": 447}])
        plan = ex.exit_position("IBM", 447, px=200.0, equity=100_000.0)
        self.assertFalse(plan.get("block_reason"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertGreater(len(plan.get("order_qtys") or []), 1)
        self.assertEqual(sum(plan["order_qtys"]), 447)

    def test_legacy_flattener_reduces_oversized_position(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_MAX_POSITION_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_AI_DRY_RUN"] = "1"
        from unified_ai.legacy_flattener import flatten_oversized_positions

        positions = [{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}]
        summary = flatten_oversized_positions(None, positions, dry_run=True)
        self.assertEqual(len(summary.get("flattened") or []), 1)
        rec = summary["flattened"][0]
        self.assertEqual(rec["symbol"], "IBM")
        self.assertGreater(rec["sell_qty"], 0)
        self.assertTrue(rec.get("chunked_exit"))


if __name__ == "__main__":
    unittest.main()
