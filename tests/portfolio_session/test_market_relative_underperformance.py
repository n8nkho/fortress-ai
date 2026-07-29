"""Unit and integration tests for market_relative_underperformance guard (SI fix)."""
from __future__ import annotations

import unittest

from utils.portfolio_session.entry_decision import evaluate_entry_decision
from utils.portfolio_session.entry_block_manager import EntryBlockManager
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
)
from utils.portfolio_session.risk_manager import reset_market_relative_cooldown
from utils.portfolio_session.session_manager import evaluate_entry_guards


class TestMarketRelativeUnderperformanceGuard(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()

    def test_blocks_when_alpha_minus_0_6_pct(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.6,
                "benchmark_change_1d_pct": 0.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_allows_when_alpha_minus_0_3_pct(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        result = guard.evaluate(
            {
                "alpha_vs_spy_pct": -0.3,
                "benchmark_change_1d_pct": 0.3,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertFalse(result.blocked)

    def test_evaluate_session_alias(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        result = guard.evaluate_session({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        self.assertTrue(result.blocked)

    def test_callable_blocks_when_alpha_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        self.assertTrue(
            guard({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True, "component": "portfolio_session"})
        )

    def test_callable_allows_when_alpha_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        self.assertFalse(
            guard({"alpha_vs_spy_pct": -0.3, "benchmark_ok": True, "component": "portfolio_session"})
        )

    def test_none_alpha_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        self.assertFalse(guard({"benchmark_ok": True, "component": "portfolio_session"}))

    def test_zero_threshold_blocks_negative_alpha(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=0.0)
        self.assertTrue(
            guard({"alpha_vs_spy_pct": -0.1, "benchmark_ok": True, "component": "portfolio_session"})
        )
        self.assertFalse(
            guard({"alpha_vs_spy_pct": 0.0, "benchmark_ok": True, "component": "portfolio_session"})
        )

    def test_entry_gate_check_entry_blocks(self) -> None:
        from utils.portfolio_session.entry_gate import EntryGate

        gate = EntryGate(
            guards=[MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)]
        )
        reasons = gate.check_entry_blocks({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        self.assertEqual(reasons, ["market_relative_underperformance"])

    def test_exact_threshold_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        result = guard.evaluate({"alpha_vs_spy_pct": -0.5, "benchmark_ok": True})
        self.assertFalse(result.blocked)

    def test_should_block_entry_returns_reason_for_session_metrics(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(threshold_alpha_vs_spy_pct=-0.5)
        reason = guard.should_block_entry({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        self.assertEqual(reason, "market_relative_underperformance")


class TestEntryBlockManager(unittest.TestCase):
    def test_build_guards_registers_underperformance_first(self) -> None:
        manager = EntryBlockManager()
        guards = manager._build_guards()
        self.assertEqual(len(guards), 1)
        self.assertEqual(guards[0].name, "market_relative_underperformance")

    def test_evaluate_blocks_increments_entry_block_breakdown(self) -> None:
        manager = EntryBlockManager()
        result = manager.evaluate_blocks(
            {
                "alpha_vs_spy_pct": -0.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "entry_block_breakdown": {"denylist": 1},
            }
        )
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "market_relative_underperformance")
        self.assertEqual(result["entry_block_breakdown"]["market_relative_underperformance"], 1)
        self.assertEqual(result["entry_block_breakdown"]["market_relative"], 1)
        self.assertEqual(result["entry_block_breakdown"]["denylist"], 1)


class TestMarketRelativeUnderperformanceIntegration(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()

    def test_entry_decision_blocks_and_increments_breakdown(self) -> None:
        decision, state = evaluate_entry_decision(
            {
                "alpha_vs_spy_pct": -0.6,
                "benchmark_change_1d_pct": 0.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "entry_block_breakdown": {"denylist": 1},
            }
        )
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "market_relative_underperformance")
        self.assertEqual(state["entry_block_breakdown"]["market_relative_underperformance"], 1)
        self.assertEqual(state["entry_block_breakdown"]["market_relative"], 1)
        self.assertEqual(state["entry_block_breakdown"]["denylist"], 1)
        self.assertEqual(state["entry_block_reason"], "market_relative_underperformance")

    def test_session_manager_blocks_underperformance(self) -> None:
        result = evaluate_entry_guards(
            {
                "alpha_vs_spy_pct": -0.6,
                "benchmark_change_1d_pct": 0.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
                "session_exit_count": 0,
                "session_realized_usd": 0.0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")


if __name__ == "__main__":
    unittest.main()
