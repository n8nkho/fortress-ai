"""Alpaca order hygiene tests."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestAlpacaOrderHygiene(unittest.TestCase):
    def test_dry_run_counts_phantom_sells(self):
        from utils.alpaca_order_hygiene import cancel_stale_open_orders

        tc = MagicMock()
        tc.get_all_positions.return_value = []
        o1 = MagicMock(symbol="SPY", side=MagicMock(value="sell"), id="a", submitted_at="2026-06-19")
        o2 = MagicMock(symbol="IWM", side=MagicMock(value="sell"), id="b", submitted_at="2026-06-19")
        tc.get_orders.return_value = [o1, o2]
        out = cancel_stale_open_orders(tc, dry_run=True)
        self.assertTrue(out.get("dry_run"))
        self.assertEqual(out.get("would_cancel"), 2)


if __name__ == "__main__":
    unittest.main()
