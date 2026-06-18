"""Position deduplication, chunked exits, and legacy flattening."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_ROOT_PATCH = patch.dict(os.environ, {}, clear=False)


class TestPositionManagerDeduplication(unittest.TestCase):
    def test_blocks_duplicate_entry_when_already_holding(self):
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447}])
        gate = pm.enter_position("IBM", 10, held_qty=447)
        self.assertIsNotNone(gate)
        self.assertFalse(gate.get("allowed"))
        self.assertEqual(gate.get("block_reason"), "already_holding")
        self.assertIn("already_holding", gate.get("detail", ""))

    def test_allows_entry_when_flat(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"FORTRESS_AI_DATA_DIR": td, "POSITION_DEDUPLICATION_ENABLED": "true"},
            ):
                from unified_ai.position_manager import PositionManager

                pm = PositionManager([])
                gate = pm.enter_position("IBM", 10, held_qty=0)
                self.assertTrue(gate.get("allowed"))

    def test_respects_deduplication_disabled(self):
        with patch.dict(os.environ, {"POSITION_DEDUPLICATION_ENABLED": "false"}):
            from importlib import reload

            import unified_ai.settings as settings

            settings.load_defaults.cache_clear()
            reload(settings)
            from unified_ai.position_manager import PositionManager

            pm = PositionManager([{"sym": "IBM", "qty": 100}])
            gate = pm.enter_position("IBM", 5, held_qty=0)
            self.assertTrue(gate.get("allowed"))


class TestPositionManagerFlatten(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def test_flatten_oversized_positions_plans_chunks(self):
        from unified_ai.position_manager import PositionManager

        pm = PositionManager([{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}])
        plan = pm.flatten_oversized_positions("IBM", max_notional=3000.0, px=200.0)
        self.assertFalse(plan.get("skipped"))
        self.assertTrue(plan.get("chunked_exit"))
        self.assertGreater(plan.get("sell_qty", 0), 0)
        self.assertEqual(sum(plan.get("order_qtys") or []), plan["sell_qty"])


class TestOrderExecutorChunkedExit(unittest.TestCase):
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


class TestLegacyFlattener(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"
        os.environ["FORTRESS_AI_DRY_RUN"] = "1"

    def test_identifies_oversized_and_plans_chunks(self):
        from unified_ai.legacy_flattener import flatten_oversized_positions

        positions = [{"sym": "IBM", "qty": 447, "mkt_value": 89400.0}]
        summary = flatten_oversized_positions(None, positions, dry_run=True)
        self.assertEqual(len(summary.get("flattened") or []), 1)
        rec = summary["flattened"][0]
        self.assertEqual(rec["symbol"], "IBM")
        self.assertGreater(rec["sell_qty"], 0)
        self.assertTrue(rec.get("chunked_exit"))

    def test_skips_positions_under_cap(self):
        from unified_ai.legacy_flattener import flatten_oversized_positions

        positions = [{"sym": "IBM", "qty": 5, "mkt_value": 1000.0}]
        summary = flatten_oversized_positions(None, positions, dry_run=True)
        self.assertEqual(summary.get("flattened"), [])
        self.assertEqual(len(summary.get("skipped") or []), 1)


class TestActIntegration(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_MAX_ORDER_NOTIONAL_USD"] = "3000"

    def test_act_blocks_repeat_entry_when_already_holding(self):
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

    def test_act_chunks_large_exit(self):
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
