"""Unit tests for market_relative_underperformance guard (SI fix 1223ddba)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.portfolio_session.entry_decision import evaluate_entry_decision
from utils.portfolio_session.entry_gate import EntryGate, evaluate_entry_blocks
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
    check_market_relative_underperformance,
    should_block_entry,
)
from utils.portfolio_session.risk_manager import (
    entry_blocked_by_market_relative,
    reset_market_relative_cooldown,
)


class TestMarketRelativeUnderperformance(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()

    def test_check_function_blocks_below_threshold(self) -> None:
        self.assertTrue(check_market_relative_underperformance(-1.5, -1.0))

    def test_should_block_entry_si_plan_thresholds(self) -> None:
        self.assertTrue(should_block_entry(-0.7, -0.5))
        self.assertFalse(should_block_entry(-0.3, -0.5))
        self.assertFalse(should_block_entry(0.2, -0.5))

    def test_entry_gate_blocks_si_plan_alpha_minus_0_70(self) -> None:
        gate = EntryGate()
        result = gate.evaluate_entry_blocks({"alpha_vs_spy_pct": -0.70, "benchmark_ok": True})
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "market_relative_underperformance")
        self.assertTrue(gate.market_relative_blocked)

    def test_guard_check_method_blocks_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -1.0})
        blocked, reason = guard.check(-1.5)
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")

    def test_guard_check_method_allows_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -1.0})
        blocked, reason = guard.check(-0.5)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_check_function_allows_above_threshold(self) -> None:
        self.assertFalse(check_market_relative_underperformance(-0.5, -1.0))

    def test_check_function_exact_threshold_does_not_block(self) -> None:
        self.assertFalse(check_market_relative_underperformance(-1.0, -1.0))

    def test_blocks_when_threshold_hit(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -1.0},
            window_seconds=300,
        )
        result = guard.evaluate(
            {
                "session_alpha_vs_spy": -1.27,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_allows_when_threshold_miss(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"market_relative_underperformance_threshold": -1.0},
            window_seconds=300,
        )
        result = guard.evaluate({"session_alpha_vs_spy": -0.8, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_exact_threshold_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"market_relative_underperformance_threshold": -1.0},
            window_seconds=300,
        )
        result = guard.evaluate({"session_alpha_vs_spy": -1.0, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_benchmark_unavailable_edge_case(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"market_relative_underperformance_threshold": -1.0},
        )
        result = guard.evaluate({"session_alpha_vs_spy": -2.0, "benchmark_ok": False})
        self.assertFalse(result.blocked)
        self.assertEqual(result.detail, "benchmark_unavailable")

    def test_missing_alpha_edge_case(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"market_relative_underperformance_threshold": -1.0},
        )
        result = guard.evaluate({"benchmark_ok": True})
        self.assertFalse(result.blocked)
        self.assertEqual(result.detail, "missing_alpha_data")

    def test_blocks_si_plan_scenario_alpha_minus_0_64_spy_plus_0_64(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"market_relative_underperformance_threshold_bps": -50},
            window_seconds=300,
        )
        result = guard.evaluate(
            {
                "session_return_pct": 0.0,
                "benchmark_change_1d_pct": 0.64,
                "alpha_vs_spy_pct": -0.64,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_evaluate_entry_blocks_pipeline(self) -> None:
        gate = EntryGate(
            guards=[
                MarketRelativeUnderperformanceGuard(
                    config={"market_relative_underperformance_threshold": -1.0},
                    window_seconds=300,
                )
            ]
        )
        blocks = gate.evaluate_entry_blocks(
            {
                "session_alpha_vs_spy": -1.27,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(blocks["market_relative_underperformance"])

    def test_evaluate_entry_blocks_module_helper(self) -> None:
        reset_market_relative_cooldown()
        gate = EntryGate(
            guards=[
                MarketRelativeUnderperformanceGuard(
                    threshold_pct=-1.0,
                    window_seconds=300,
                )
            ]
        )
        with patch(
            "utils.portfolio_session.entry_gate.get_entry_gate",
            return_value=gate,
        ):
            blocks = evaluate_entry_blocks(
                {
                    "session_alpha_vs_spy": -1.5,
                    "benchmark_ok": True,
                    "component": "portfolio_session",
                    "session_exit_count": 0,
                    "session_realized_usd": 0.0,
                }
            )
        self.assertTrue(blocks["market_relative_underperformance"])

    def test_entry_decision_blocks_when_underperforming(self) -> None:
        decision, state = evaluate_entry_decision(
            {
                "alpha_vs_spy_pct": -0.70,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "market_relative_underperformance")
        self.assertIn("market_relative", state.get("entry_block_breakdown") or {})

    def test_entry_decision_allows_when_disabled(self) -> None:
        with patch(
            "utils.portfolio_session.entry_decision.get_market_relative_underperformance_enabled",
            return_value=False,
        ):
            decision, _state = evaluate_entry_decision(
                {
                    "alpha_vs_spy_pct": -2.0,
                    "benchmark_ok": True,
                }
            )
        self.assertFalse(decision.blocked)

    def test_entry_blocked_by_market_relative_helper(self) -> None:
        blocked, reason = entry_blocked_by_market_relative(
            {
                "session_alpha_vs_spy": -1.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")


if __name__ == "__main__":
    unittest.main()
