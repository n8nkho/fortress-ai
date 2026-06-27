"""Broker reconciliation diagnostic tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestBrokerReconciliation(unittest.TestCase):
    def test_premature_exit_ledger_detects_estimate_rows(self):
        from datetime import datetime, timezone

        from utils import broker_reconciliation as br

        recent = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "pnl_ledger.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "timestamp": recent,
                        "symbol": "IWM",
                        "pnl": 55.77,
                        "note": "exit_fill_pnl_estimate",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(br, "_LEDGER", ledger):
                findings = br.scan_premature_exit_ledger()
        self.assertEqual(findings[0]["code"], "premature_exit_ledger")
        self.assertIn("IWM", findings[0]["symbols"])

    def test_operator_drift_when_broker_exceeds_swarm(self):
        from utils import broker_reconciliation as br

        with tempfile.TemporaryDirectory() as td:
            op = Path(td) / "latest.json"
            op.write_text(
                json.dumps(
                    {
                        "portfolio": {
                            "open_positions": 0,
                            "skim_open_positions": 0,
                            "broker_open_positions": 4,
                            "broker_symbols": ["IWM", "QQQ"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(br, "_OPERATOR", op):
                findings = br.scan_operator_broker_open_drift()
        self.assertEqual(findings[0]["code"], "operator_broker_open_drift")


class TestExitFillGuard(unittest.TestCase):
    def test_exit_not_executed_when_poll_shows_zero_fill(self):
        import os
        from unittest.mock import MagicMock

        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        mock_ticker = MagicMock()
        mock_ticker.fast_info.get.return_value = 200.0
        with patch("agents.unified_ai_agent._core._dry_run", return_value=False):
            with patch("agents.unified_ai_agent._core._min_confidence_execute", return_value=0.5):
                with patch("agents.unified_ai_agent._core.yf.Ticker", return_value=mock_ticker):
                    with patch(
                        "agents.unified_ai_agent._core.evaluate_pre_trade_submission",
                        return_value={"allowed": True},
                    ):
                        with patch("agents.unified_ai_agent._core._alpaca_client") as mock_tc:
                            with patch(
                                "utils.alpaca_order_confirm.poll_order_fill",
                                return_value={"filled_qty": 0, "status": "accepted", "timeout": True},
                            ):
                                with patch("agents.unified_ai_agent.exit_handler.handle_exit_ledger") as mock_ledger:
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
                                            "parameters": {"symbol": "IWM", "qty": 10},
                                        },
                                        {
                                            "equity": 100_000.0,
                                            "positions": [{"sym": "IWM", "qty": 10, "unrealized_pl": 5.0}],
                                        },
                                        {},
                                    )
        self.assertFalse(result.get("executed"))
        self.assertEqual(result.get("block_reason"), "exit_unfilled")
        mock_ledger.assert_not_called()


if __name__ == "__main__":
    unittest.main()
