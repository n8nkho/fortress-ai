"""Unit tests for unified_ai_agent exit ledger package."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestUnifiedAIAgentExitLedger(unittest.TestCase):
    def test_handle_exit_ledger_blocks_unfilled(self):
        from agents.unified_ai_agent.exit_handler import EXIT_UNFILLED, handle_exit_ledger

        with patch("agents.unified_ai_agent.ledger.record_confirmed_exit_fill") as mock_rec:
            out = handle_exit_ledger(
                {
                    "symbol": "NVDA",
                    "fill_status": EXIT_UNFILLED,
                    "pnl_usd": 4.0,
                    "side": "SELL",
                    "qty": 2,
                }
            )
        self.assertFalse(out["recorded"])
        mock_rec.assert_not_called()

    def test_record_confirmed_exit_fill_writes_ledger(self):
        from agents.unified_ai_agent.ledger import record_confirmed_exit_fill
        from agents.unified_ai_agent.exit_handler import EXIT_FILL_CONFIRMED

        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "pnl_ledger.jsonl"
            with patch("utils.ai_pnl_ledger.ledger_path", return_value=ledger):
                out = record_confirmed_exit_fill(
                    {
                        "symbol": "AMD",
                        "fill_status": EXIT_FILL_CONFIRMED,
                        "pnl_usd": 2.5,
                        "side": "SELL",
                        "qty": 1,
                        "order_id": "x1",
                    }
                )
            row = json.loads(ledger.read_text(encoding="utf-8").strip())
        self.assertTrue(out["recorded"])
        self.assertEqual(row["note"], EXIT_FILL_CONFIRMED)

    def test_reconcile_premature_exits_reports_still_held(self):
        from datetime import datetime, timezone

        from agents.unified_ai_agent.reconciliation import reconcile_premature_exits

        recent = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "pnl_ledger.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "timestamp": recent,
                        "symbol": "IWM",
                        "pnl": 1.0,
                        "note": "exit_fill_pnl_estimate",
                        "source": "unified_ai_agent",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch(
                "agents.unified_ai_agent.reconciliation._ledger_path",
                return_value=ledger,
            ):
                report = reconcile_premature_exits(broker_symbols={"IWM", "QQQ"})
        self.assertFalse(report["ok"])
        self.assertEqual(report["count"], 1)
        self.assertTrue(report["corrections"][0]["still_held_at_broker"])


if __name__ == "__main__":
    unittest.main()
