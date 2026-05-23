"""Alpaca order side must be buy/sell for MarketOrderRequest (unified agent)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestUnifiedAiActSide(unittest.TestCase):
    def test_submit_uses_lowercase_side(self):
        mock_ticker = MagicMock()
        mock_ticker.fast_info.get.return_value = 40.0
        with patch("agents.unified_ai_agent._dry_run", return_value=False):
            with patch("agents.unified_ai_agent._min_confidence_execute", return_value=0.5):
                with patch("agents.unified_ai_agent.yf.Ticker", return_value=mock_ticker):
                    with patch(
                        "agents.unified_ai_agent.evaluate_pre_trade_submission",
                        return_value={"allowed": True},
                    ):
                        with patch("agents.unified_ai_agent._alpaca_client") as mock_tc:
                            tc = MagicMock()
                            mock_tc.return_value = tc
                            order = MagicMock()
                            order.id = "oid"
                            order.status = "accepted"
                            tc.submit_order.return_value = order

                            from agents.unified_ai_agent import act

                            act(
                                {
                                    "action": "enter_position",
                                    "confidence": 0.8,
                                    "parameters": {"symbol": "HRL", "qty": 10},
                                },
                                {"equity": 100_000.0},
                                {},
                            )
                            req = tc.submit_order.call_args[0][0]
                            self.assertEqual(getattr(req, "side", None), "buy")


if __name__ == "__main__":
    unittest.main()
