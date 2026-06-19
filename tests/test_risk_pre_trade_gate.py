"""Risk-layer duplicate entry gate tests."""
from __future__ import annotations

import os
import unittest
from unittest import mock


class TestRiskDuplicateEntryGate(unittest.TestCase):
    def test_blocks_buy_when_already_holding(self):
        from risk.pre_trade_gate import evaluate_duplicate_entry_gate

        gate = evaluate_duplicate_entry_gate(
            side="BUY",
            symbol="IBM",
            positions=[{"sym": "IBM", "qty": 447}],
            held_qty=447,
        )
        self.assertFalse(gate.get("allowed"))
        self.assertEqual(gate.get("block_reason"), "already_holding")
        self.assertIn("duplicate_entry_accumulation", gate.get("reasons") or [])

    def test_allows_buy_when_flat(self):
        from risk.pre_trade_gate import evaluate_duplicate_entry_gate

        gate = evaluate_duplicate_entry_gate(
            side="BUY",
            symbol="IBM",
            positions=[],
            held_qty=0,
        )
        self.assertTrue(gate.get("allowed"))

    def test_allows_sell_when_holding(self):
        from risk.pre_trade_gate import evaluate_duplicate_entry_gate

        gate = evaluate_duplicate_entry_gate(
            side="SELL",
            symbol="IBM",
            positions=[{"sym": "IBM", "qty": 447}],
        )
        self.assertTrue(gate.get("allowed"))

    def test_respects_disable_env(self):
        with mock.patch.dict(os.environ, {"POSITION_DEDUPLICATION_ENABLED": "false"}):
            from unified_ai import settings

            settings.load_defaults.cache_clear()
            from risk.pre_trade_gate import evaluate_duplicate_entry_gate

            gate = evaluate_duplicate_entry_gate(
                side="BUY",
                symbol="IBM",
                positions=[{"sym": "IBM", "qty": 447}],
            )
            self.assertTrue(gate.get("allowed"))


class TestRiskPositionManagerFlatten(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def test_flatten_oversized_positions_plans_chunks(self):
        from risk.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}])
        plan = pm.flatten_oversized_positions("IBM", max_notional=3000.0, px=200.0)
        self.assertFalse(plan.get("skipped"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertEqual(sum(plan.get("order_qtys") or []), plan["sell_qty"])

    def test_exit_position_plans_chunks(self):
        from risk.position_manager import PositionManager

        pm = PositionManager()
        plan = pm.exit_position("IBM", 447, mark_price=200.0, max_notional=3000.0)
        self.assertTrue(plan.get("chunked_exit"))
        self.assertEqual(sum(plan.get("order_qtys") or []), 447)

    def test_chunk_exit_orders_splits_qty(self):
        from risk.position_manager import PositionManager

        pm = PositionManager()
        order_qtys = pm.chunk_exit_orders("IBM", 447, 3000.0, mark_price=200.0)
        self.assertGreater(len(order_qtys), 1)
        self.assertEqual(sum(order_qtys), 447)
        self.assertTrue(all(q * 200.0 <= 3000.0 for q in order_qtys))


if __name__ == "__main__":
    unittest.main()
