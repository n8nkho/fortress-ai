"""Alpaca order side must be buy/sell for MarketOrderRequest."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestSkimActSide(unittest.TestCase):
    def test_submit_uses_lowercase_side(self):
        with patch("agents.skim_swarm.act._alpaca_client") as mock_tc:
            with patch("agents.skim_swarm.act._last_price", return_value=100.0):
                with patch("agents.skim_swarm.act.dry_run", return_value=False):
                    with patch(
                        "agents.skim_swarm.act.evaluate_pre_trade_submission",
                        return_value={"allowed": True},
                    ):
                        tc = MagicMock()
                        mock_tc.return_value = tc
                        order = MagicMock()
                        order.id = "oid"
                        order.status = "accepted"
                        tc.submit_order.return_value = order

                        from agents.skim_swarm.act import act

                        act(
                            {"action": "enter_long"},
                            symbol="AAPL",
                            equity=50000.0,
                            position={"side": "flat", "qty": 0},
                        )
                        req = tc.submit_order.call_args[0][0]
                        self.assertEqual(getattr(req, "side", None), "buy")


if __name__ == "__main__":
    unittest.main()
