"""Tests for operator broker vs ledger reconciliation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestOperatorBrokerReconcile(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name
        os.environ["FORTRESS_RECONCILE_BROKER_LEDGER"] = "1"
        os.environ["FORTRESS_RECONCILE_BROKER_LEDGER_COOLDOWN_SEC"] = "0"

    def test_reconcile_broker_ledger_orphan_close(self):
        from utils import operator_broker_reconcile as obr

        broker_positions = {
            "AIQ": {"symbol": "AIQ", "qty": 12, "side": "long"},
            "QQQ": {"symbol": "QQQ", "qty": 5, "side": "long"},
        }
        ledger = {"QQQ": "skim_swarm"}

        mock_tc = MagicMock()
        mock_order = MagicMock()
        mock_order.id = "ord-aiq"
        mock_tc.submit_order.return_value = mock_order

        with patch.object(obr, "fetch_broker_positions", return_value=broker_positions):
            with patch.object(obr, "fetch_ledger_open_symbols", return_value=ledger):
                with patch.object(obr, "_mark_ledger_stale") as mock_stale:
                    with patch("utils.skim_swarm_config.dry_run", return_value=False):
                        with patch("utils.alpaca_env.alpaca_credentials", return_value=("k", "s")):
                            with patch("utils.alpaca_env.alpaca_trading_client_kwargs", return_value={}):
                                with patch("alpaca.trading.client.TradingClient", return_value=mock_tc):
                                    with patch(
                                        "utils.alpaca_execution.gate_exit_submission",
                                        return_value=None,
                                    ):
                                        report = obr.reconcile_broker_ledger(force=True)

        self.assertEqual(report["orphan_symbols"], ["AIQ"])
        self.assertEqual(report["stale_symbols"], [])
        mock_stale.assert_not_called()
        mock_tc.submit_order.assert_called_once()
        req = mock_tc.submit_order.call_args[0][0]
        self.assertEqual(getattr(req, "symbol", None), "AIQ")
        self.assertEqual(int(getattr(req, "qty", 0)), 12)
        self.assertEqual(str(getattr(req, "side", "")).lower().replace("orderside.", ""), "sell")

    def test_reconcile_broker_ledger_no_drift(self):
        from utils import operator_broker_reconcile as obr

        positions = {
            "QQQ": {"symbol": "QQQ", "qty": 5, "side": "long"},
        }
        ledger = {"QQQ": "skim_swarm"}

        with patch.object(obr, "fetch_broker_positions", return_value=positions):
            with patch.object(obr, "fetch_ledger_open_symbols", return_value=ledger):
                with patch.object(obr, "_submit_orphan_close") as mock_close:
                    with patch.object(obr, "_mark_ledger_stale") as mock_stale:
                        report = obr.reconcile_broker_ledger(force=True)

        self.assertEqual(report["orphan_symbols"], [])
        self.assertEqual(report["stale_symbols"], [])
        mock_close.assert_not_called()
        mock_stale.assert_not_called()
        self.assertIsNone(report.get("block_reason"))

    def test_cooldown_skips_second_run(self):
        from utils import operator_broker_reconcile as obr

        with patch.object(obr, "fetch_broker_positions", return_value={}):
            with patch.object(obr, "fetch_ledger_open_symbols", return_value={}):
                first = obr.reconcile_broker_ledger(force=True)
                os.environ["FORTRESS_RECONCILE_BROKER_LEDGER_COOLDOWN_SEC"] = "3600"
                second = obr.maybe_reconcile_broker_ledger()

        self.assertNotIn("skipped", first)
        self.assertEqual(second.get("skipped"), "cooldown")

    def test_stale_ledger_marked_when_broker_missing(self):
        from utils import operator_broker_reconcile as obr

        with patch.object(obr, "fetch_broker_positions", return_value={}):
            with patch.object(obr, "fetch_ledger_open_symbols", return_value={"IWM": "infra_swarm"}):
                with patch.object(obr, "_submit_orphan_close") as mock_close:
                    with patch.object(obr, "_mark_ledger_stale", return_value={"marked_stale": True}) as mock_stale:
                        report = obr.reconcile_broker_ledger(force=True)

        mock_close.assert_not_called()
        mock_stale.assert_called_once_with("IWM", "infra_swarm")
        self.assertEqual(report["stale_symbols"], ["IWM"])

    def test_adopt_tracked_orphan_before_close(self):
        from utils import operator_broker_reconcile as obr

        broker_positions = {
            "MSFT": {"symbol": "MSFT", "qty": 1, "side": "long"},
        }

        with patch.object(obr, "fetch_broker_positions", return_value=broker_positions):
            with patch.object(obr, "fetch_ledger_open_symbols", return_value={}):
                with patch.object(obr, "_try_adopt_tracked_orphan", return_value={"symbol": "MSFT", "adopted": True}) as mock_adopt:
                    with patch.object(obr, "_submit_orphan_close") as mock_close:
                        report = obr.reconcile_broker_ledger(force=True)

        mock_adopt.assert_called_once()
        mock_close.assert_not_called()
        self.assertEqual(report["orphan_symbols"], [])


if __name__ == "__main__":
    unittest.main()
