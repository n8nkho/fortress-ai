"""Skim exit sizing — allow position qty > max_shares entry cap."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class TestSkimActExitQty(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def test_exit_two_shares_when_max_shares_one(self):
        with patch("agents.skim_swarm.act._alpaca_client") as mock_tc:
            with patch("agents.skim_swarm.act._last_price", return_value=500.0):
                with patch("agents.skim_swarm.act.dry_run", return_value=False):
                    with patch("agents.skim_swarm.act.max_shares", return_value=1):
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

                            result = act(
                                {"action": "exit_position"},
                                symbol="SPY",
                                equity=100_000.0,
                                position={"side": "long", "qty": 2},
                            )
                            self.assertTrue(result.get("executed"))
                            self.assertNotEqual(result.get("block_reason"), "qty_invalid")
                            req = tc.submit_order.call_args[0][0]
                            self.assertEqual(getattr(req, "qty", None), 2)


if __name__ == "__main__":
    unittest.main()
