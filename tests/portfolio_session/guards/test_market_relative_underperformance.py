"""Unit tests for MarketRelativeUnderperformanceGuard."""
from __future__ import annotations

import unittest

from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
)
from utils.portfolio_session.risk_manager import reset_market_relative_cooldown
from utils.portfolio_session.session_manager import evaluate_entry_guards


class TestMarketRelativeUnderperformanceGuard(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()

    def test_check_session_context_blocks_si_plan_alpha_minus_0_6(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -0.5})
        result = guard.check_session_context({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_check_session_context_allows_si_plan_alpha_minus_0_4(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -0.5})
        result = guard.check_session_context({"alpha_vs_spy_pct": -0.4, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_check_blocks_when_alpha_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        blocked, reason = guard.check(-0.8)
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")

    def test_check_allows_when_alpha_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        blocked, reason = guard.check(-0.3)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_check_zero_alpha_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        blocked, reason = guard.check(0.0)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_check_missing_data_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        blocked, reason = guard.check(session_state={"benchmark_ok": True})
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_blocks_when_threshold_crossed(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.8,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_blocks_si_finding_scenario(self) -> None:
        """Session alpha -0.92% vs SPY +0.92% blocks at -0.5% threshold."""
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.9205,
                "benchmark_change_1d_pct": 0.9205,
                "session_return_pct": 0.0,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_allows_when_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate({"alpha_vs_spy_pct": -0.3, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_zero_alpha_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate({"alpha_vs_spy_pct": 0.0, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_missing_alpha_data_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate({"benchmark_ok": True})
        self.assertFalse(result.blocked)
        self.assertEqual(result.detail, "missing_alpha_data")

    def test_benchmark_unavailable_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate({"alpha_vs_spy_pct": -2.0, "benchmark_ok": False})
        self.assertFalse(result.blocked)
        self.assertEqual(result.detail, "benchmark_unavailable")

    def test_skipped_when_disabled(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            threshold_pct=-0.5, window_seconds=300, enabled=False
        )
        result = guard.evaluate({"alpha_vs_spy_pct": -2.0, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_session_manager_sets_entry_block_breakdown(self) -> None:
        from utils.portfolio_session import session_manager

        state = {
            "alpha_vs_spy_pct": -1.6,
            "benchmark_ok": True,
            "component": "portfolio_session",
            "session_exit_count": 0,
            "session_realized_usd": 0.0,
            "entry_block_breakdown": {"denylist": 2},
        }
        result, updated = session_manager._evaluate_entry_guards(state)
        self.assertTrue(result.blocked)
        self.assertEqual(updated["entry_block_breakdown"]["market_relative_underperformance"], 1)
        self.assertEqual(updated["entry_block_breakdown"]["market_relative"], 1)
        self.assertEqual(updated["entry_block_breakdown"]["denylist"], 2)

    def test_evaluate_entry_guards_public_api(self) -> None:
        result = evaluate_entry_guards(
            {
                "alpha_vs_spy_pct": -1.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)

    def test_blocks_si_plan_scenario_alpha_minus_0_64_spy_plus_0_64(self) -> None:
        """Portfolio flat vs SPY +0.64% => alpha -0.64%; blocks at -0.5% threshold."""
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.64,
                "benchmark_change_1d_pct": 0.64,
                "session_return_pct": 0.0,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_exact_threshold_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -0.5})
        result = guard.evaluate({"alpha_vs_spy_pct": -0.5, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_should_block_entry_returns_reason_for_session_metrics(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -0.5})
        reason = guard.should_block_entry({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        self.assertEqual(reason, "market_relative_underperformance")

    def test_should_block_entry_returns_none_when_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(config={"underperformance_threshold_pct": -0.5})
        self.assertIsNone(
            guard.should_block_entry({"alpha_vs_spy_pct": -0.4, "benchmark_ok": True})
        )

    def test_loads_underperformance_threshold_from_portfolio_session_yaml(self) -> None:
        from utils.portfolio_session.risk_manager import load_market_relative_gate_config

        cfg = load_market_relative_gate_config()
        self.assertIn("threshold_pct", cfg)
        self.assertLessEqual(float(cfg["threshold_pct"]), 0.0)


if __name__ == "__main__":
    unittest.main()
