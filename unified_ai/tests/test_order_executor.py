"""Unit tests for unified_ai.order_executor."""
from __future__ import annotations

import os
import unittest


class TestOrderExecutor(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def test_chunks_large_exit(self):
        from unified_ai.order_executor import OrderExecutor

        ex = OrderExecutor([{"sym": "IBM", "qty": 447}])
        plan = ex.exit_position("IBM", 447, px=200.0, equity=100_000.0)
        self.assertFalse(plan.get("block_reason"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertGreater(len(plan.get("order_qtys") or []), 1)
        self.assertEqual(sum(plan["order_qtys"]), 447)

    def test_no_chunk_when_under_cap(self):
        from unified_ai.order_executor import OrderExecutor

        ex = OrderExecutor([{"sym": "IBM", "qty": 5}])
        plan = ex.exit_position("IBM", 5, px=200.0, equity=100_000.0)
        self.assertEqual(plan.get("order_qtys"), [5])
        self.assertFalse(plan.get("chunked_exit"))


if __name__ == "__main__":
    unittest.main()
