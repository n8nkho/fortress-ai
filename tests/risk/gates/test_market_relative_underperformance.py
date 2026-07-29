"""Unit tests for market_relative_underperformance guard."""
from __future__ import annotations

import unittest

from risk.guards.market_relative_underperformance import MarketRelativeUnderperformanceGuard
from risk.guard_engine import evaluate_entry, evaluate_entry_guards
from utils.portfolio_session.risk_manager import RiskManager, reset_market_relative_cooldown
from utils.portfolio_session.gates.market_relative_gate import MarketRelativeGate


class TestMarketRelativeUnderperformanceGuard(unittest.TestCase):
    def test_blocks_when_alpha_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=-0.5, lookback_minutes=60)
        self.assertTrue(guard.should_block(-0.8, -0.5))
        result = guard.evaluate(
            {
                "session_alpha_vs_spy": -0.8,
                "benchmark_ok": True,
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_allows_when_alpha_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=-0.5, lookback_minutes=60)
        self.assertFalse(guard.should_block(-0.3, -0.5))
        result = guard.evaluate(-0.3)
        self.assertFalse(result.blocked)

    def test_decimal_threshold_blocks_at_half_percent(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold=0.005, lookback_minutes=60)
        self.assertTrue(guard.should_block(-0.6))
        self.assertFalse(guard.should_block(-0.4))

    def test_respects_cooldown(self) -> None:
        reset_market_relative_cooldown()
        gate = MarketRelativeGate(max_underperformance_pct=-0.5, lookback_minutes=60)
        manager = RiskManager(gates=[gate], cooldown_seconds=3600)
        first = manager.evaluate_pre_trade_gates(
            {
                "session_alpha_vs_spy": -1.0,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(first.blocked)
        second = manager.evaluate_pre_trade_gates({"session_alpha_vs_spy": 0.5, "benchmark_ok": True})
        self.assertTrue(second.blocked)
        self.assertIn("cooldown_seconds", second.detail)

    def test_guard_engine_sets_entry_block_reason(self) -> None:
        reset_market_relative_cooldown()
        result = evaluate_entry_guards(
            {
                "session_alpha_vs_spy": -1.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result["blocked"])
        self.assertEqual(result["entry_block_reason"], "market_relative_underperformance")
        self.assertEqual(result["denylist"], ["*"])

    def test_evaluate_entry_increments_entry_block_breakdown(self) -> None:
        reset_market_relative_cooldown()
        result = evaluate_entry(
            {
                "session_alpha_vs_spy": -1.0,
                "benchmark_ok": True,
                "entry_block_breakdown": {"pattern_disables": 2},
            }
        )
        self.assertTrue(result["blocked"])
        breakdown = result["entry_block_breakdown"]
        self.assertEqual(breakdown["market_relative"], 1)
        self.assertEqual(breakdown["pattern_disables"], 2)


if __name__ == "__main__":
    unittest.main()
