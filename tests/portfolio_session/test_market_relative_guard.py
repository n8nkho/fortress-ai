"""Unit tests for market_relative_underperformance guard (SI fix 067b0280)."""
from __future__ import annotations

import unittest

from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
)
from utils.portfolio_session.risk_manager import reset_market_relative_cooldown
from utils.portfolio_session.session_manager import evaluate_entry_guards


class TestMarketRelativeGuard(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()

    def test_blocks_when_alpha_vs_spy_minus_0_005_at_threshold_0_004(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.004)
        blocked, reason = guard.check(-0.5)
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")

    def test_does_not_block_when_alpha_vs_spy_minus_0_003(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.004)
        blocked, reason = guard.check(-0.3)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_does_not_block_when_alpha_vs_spy_outperforms(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.004)
        blocked, reason = guard.check(1.0)
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_underperformance_below_threshold_no_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_pct=-0.5, window_seconds=300)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.3,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertFalse(result.blocked)

    def test_underperformance_above_threshold_blocks(self) -> None:
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

    def test_si_finding_scenario_alpha_minus_0_42_blocks_at_default_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.004)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.4197,
                "benchmark_change_1d_pct": 0.4197,
                "session_return_pct": 0.0,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_session_manager_increments_entry_block_breakdown(self) -> None:
        result = evaluate_entry_guards(
            {
                "alpha_vs_spy_pct": -0.80,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
                "entry_block_breakdown": {"denylist": 1},
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")


if __name__ == "__main__":
    unittest.main()
