"""Edge quality gates, brackets, scorecard."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.edge_quality import (
    bracket_prices,
    breakeven_payoff,
    cost_admission_ok,
    evaluate_entry_edge_gates,
    payoff_ratio,
    profit_factor,
    rr_admission_ok,
    time_stop_triggered,
)
from utils.edge_quality_config import (
    bracket_exits_enabled,
    edge_quality_enabled,
    rr_gate_enabled,
)
from utils.edge_scorecard import compute_scorecard_from_decisions


class TestPayoffMath(unittest.TestCase):
    def test_breakeven_payoff(self):
        self.assertAlmostEqual(breakeven_payoff(0.5), 1.0, places=3)
        self.assertGreater(breakeven_payoff(0.45), 1.2)

    def test_payoff_and_pf(self):
        self.assertAlmostEqual(payoff_ratio(0.30, -0.40), 0.75, places=2)
        self.assertAlmostEqual(profit_factor(30.0, -40.0), 0.75, places=2)


class TestRrGate(unittest.TestCase):
    def test_rr_blocks_tight_stop(self):
        with patch.dict(os.environ, {"FORTRESS_EDGE_QUALITY": "1", "FORTRESS_RR_GATE": "1"}):
            ok, reason, _ = rr_admission_ok(target_usd=0.30, stop_usd=0.40, win_rate=0.45)
            self.assertFalse(ok)
            self.assertIn("edge_rr_gate", reason or "")

    def test_rr_passes_healthy(self):
        with patch.dict(os.environ, {"FORTRESS_EDGE_QUALITY": "1", "FORTRESS_RR_GATE": "1"}):
            ok, _, _ = rr_admission_ok(target_usd=0.50, stop_usd=0.35, win_rate=0.45)
            self.assertTrue(ok)


class TestCostGate(unittest.TestCase):
    def test_cost_blocks_tiny_target(self):
        with patch.dict(
            os.environ,
            {"FORTRESS_EDGE_QUALITY": "1", "FORTRESS_COST_GATE": "1", "FORTRESS_COST_GATE_MULT": "2.5"},
        ):
            ok, reason, _ = cost_admission_ok(target_usd=0.05, last=100.0, spread_bps=5.0)
            self.assertFalse(ok)
            self.assertIn("edge_cost_gate", reason or "")


class TestTimeStop(unittest.TestCase):
    def test_time_stop_fires(self):
        with patch.dict(os.environ, {"FORTRESS_EDGE_QUALITY": "1", "FORTRESS_TIME_STOP": "1", "FORTRESS_TIME_STOP_SEC": "60"}):
            entry = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            self.assertTrue(
                time_stop_triggered(
                    {"entry_ts": entry},
                    unrealized=0.02,
                    target_usd=0.40,
                )
            )

    def test_time_stop_skips_when_progress(self):
        with patch.dict(os.environ, {"FORTRESS_EDGE_QUALITY": "1", "FORTRESS_TIME_STOP": "1"}):
            entry = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
            self.assertFalse(
                time_stop_triggered(
                    {"entry_ts": entry},
                    unrealized=0.30,
                    target_usd=0.40,
                )
            )


class TestBracketPrices(unittest.TestCase):
    def test_long_bracket(self):
        tp, sl = bracket_prices(side="long", entry_price=100.0, target_usd=0.35, stop_usd=0.40)
        self.assertEqual(tp, 100.35)
        self.assertEqual(sl, 99.60)

    def test_short_bracket(self):
        tp, sl = bracket_prices(side="short", entry_price=50.0, target_usd=0.30, stop_usd=0.35)
        self.assertEqual(tp, 49.70)
        self.assertEqual(sl, 50.35)


class TestEdgeGatesIntegration(unittest.TestCase):
    def test_evaluate_passes_with_gates_off(self):
        with patch.dict(os.environ, {"FORTRESS_EDGE_QUALITY": "0"}):
            ok, _, _ = evaluate_entry_edge_gates(
                symbol="AAPL",
                pattern="momentum_long",
                side="long",
                features={"last": 180.0, "spread_bps": 3.0},
                target_usd=0.40,
                stop_usd=0.35,
            )
            self.assertTrue(ok)


class TestScorecard(unittest.TestCase):
    def test_scorecard_from_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "decisions.jsonl"
            wave = {
                "ts": "2026-05-29T15:00:00-04:00",
                "results": [
                    {
                        "symbol": "AAPL",
                        "decision": {"action": "enter_long", "reasoning": "momentum_long score=0.5"},
                        "act": {"executed": True},
                        "features": {},
                    },
                    {
                        "symbol": "AAPL",
                        "decision": {"action": "exit_position", "reasoning": "skim_target_hit:0.4"},
                        "act": {"executed": True},
                        "features": {"unrealized_usd": 0.35},
                    },
                ],
            }
            p.write_text(json.dumps(wave) + "\n", encoding="utf-8")
            sc = compute_scorecard_from_decisions(p, session_date="2026-05-29")
            self.assertTrue(sc.get("ok"))
            self.assertEqual(sc["exits"], 1)
            self.assertAlmostEqual(sc["sum_pnl_usd"], 0.35, places=2)


class TestAlpacaExecution(unittest.TestCase):
    def test_bracket_submit_mock(self):
        with patch("utils.alpaca_execution.trading_client") as mock_tc:
            with patch.dict(
                os.environ,
                {
                    "FORTRESS_EDGE_QUALITY": "1",
                    "FORTRESS_BRACKET_EXITS": "1",
                    "FORTRESS_PASSIVE_ENTRY": "0",
                },
            ):
                tc = MagicMock()
                mock_tc.return_value = tc
                order = MagicMock()
                order.id = "oid"
                order.status = "accepted"
                tc.submit_order.return_value = order

                from utils.alpaca_execution import submit_entry_with_bracket

                r = submit_entry_with_bracket(
                    symbol="AAPL",
                    side="BUY",
                    qty=1,
                    entry_price=100.0,
                    target_usd=0.35,
                    stop_usd=0.40,
                )
                self.assertTrue(r.get("executed"))
                req = tc.submit_order.call_args[0][0]
                self.assertTrue(hasattr(req, "order_class"))
                self.assertIn("bracket", str(r["detail"].get("order_type", "")))


class TestSkimSignalEdgeGate(unittest.TestCase):
    def test_entry_blocked_by_rr(self):
        with patch.dict(
            os.environ,
            {
                "FORTRESS_EDGE_QUALITY": "1",
                "FORTRESS_RR_GATE": "1",
                "FORTRESS_COST_GATE": "0",
                "FORTRESS_EXPECTANCY_GATE": "0",
            },
        ):
            from agents.skim_swarm.signal import decide

            features = {
                "symbol": "TEST",
                "side": "flat",
                "last": 50.0,
                "r1m": -0.002,
                "r5m": -0.01,
                "residual_vs_spy": 0,
                "rsi1m": 45,
                "spread_bps": 2.0,
                "atr1m": 0.01,
            }
            out = decide(
                features,
                {},
                swarm_halted=False,
                open_positions=0,
                max_open=5,
            )
            if out.get("action") in ("enter_long", "enter_short"):
                self.assertNotIn("edge_rr_gate", str(out.get("reasoning") or ""))


if __name__ == "__main__":
    unittest.main()
