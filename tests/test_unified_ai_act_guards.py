"""Unified agent position guards and chunked exits."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class TestUnifiedAiActGuards(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def test_blocks_repeat_entry_when_already_holding(self):
        mock_ticker = MagicMock()
        mock_ticker.fast_info.get.return_value = 200.0
        with patch("agents.unified_ai_agent._dry_run", return_value=False):
            with patch("agents.unified_ai_agent._min_confidence_execute", return_value=0.5):
                with patch("agents.unified_ai_agent.yf.Ticker", return_value=mock_ticker):
                    from agents.unified_ai_agent import act

                    result = act(
                        {
                            "action": "enter_position",
                            "confidence": 0.9,
                            "parameters": {"symbol": "IBM", "qty": 10},
                        },
                        {"equity": 100_000.0, "positions": [{"sym": "IBM", "qty": 447}]},
                        {},
                    )
                    self.assertFalse(result.get("executed"))
                    self.assertEqual(result.get("block_reason"), "already_holding")

    def test_chunks_large_exit(self):
        mock_ticker = MagicMock()
        mock_ticker.fast_info.get.return_value = 200.0
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

                            result = act(
                                {
                                    "action": "exit_position",
                                    "confidence": 0.9,
                                    "parameters": {"symbol": "IBM", "qty": 447},
                                },
                                {"equity": 100_000.0, "positions": [{"sym": "IBM", "qty": 447}]},
                                {},
                            )
                            self.assertTrue(result.get("executed"))
                            self.assertTrue(result.get("chunked_exit"))
                            self.assertGreater(tc.submit_order.call_count, 1)


if __name__ == "__main__":
    unittest.main()
