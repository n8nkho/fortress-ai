"""Unit tests for portfolio session entry guards."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.portfolio_session.config import get_market_relative_underperformance_threshold
from utils.portfolio_session.entry_guards import (
    evaluate_entry_blocks,
    market_relative_underperformance_gate,
)
from utils.portfolio_session.entry_guards.market_relative_underperformance import (
    check_market_relative_underperformance,
)
from utils.portfolio_session.entry_guard_manager import evaluate_entry_guard_blocks
from utils.portfolio_session.risk_manager import reset_market_relative_cooldown
from utils.portfolio_session.session_monitor import reset_session_monitor
from utils.portfolio_session.session_state import SessionState


class TestCheckMarketRelativeUnderperformance(unittest.TestCase):
    def test_returns_reason_when_alpha_below_threshold(self) -> None:
        reason = check_market_relative_underperformance(
            {"alpha_vs_spy_pct": -0.8, "benchmark_ok": True},
            -0.5,
        )
        self.assertIsNotNone(reason)
        self.assertIn("market_relative_underperformance", reason)
        self.assertIn("session_underperforming", reason)

    def test_returns_none_when_alpha_above_threshold(self) -> None:
        reason = check_market_relative_underperformance(
            {"alpha_vs_spy_pct": -0.3, "benchmark_ok": True},
            -0.5,
        )
        self.assertIsNone(reason)

    def test_entry_guard_manager_increments_breakdown(self) -> None:
        result = evaluate_entry_guard_blocks({"alpha_vs_spy_pct": -0.8, "benchmark_ok": True})
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "market_relative_underperformance")
        self.assertEqual(result["entry_block_breakdown"]["market_relative_underperformance"], 1)
        self.assertEqual(result["entry_block_breakdown"]["market_relative"], 1)


class TestMarketRelativeUnderperformanceGate(unittest.TestCase):
    def test_blocks_when_alpha_below_threshold(self) -> None:
        self.assertTrue(market_relative_underperformance_gate(-0.8, threshold=-0.5))

    def test_allows_when_alpha_above_threshold(self) -> None:
        self.assertFalse(market_relative_underperformance_gate(-0.3, threshold=-0.5))

    def test_allows_at_threshold_boundary(self) -> None:
        self.assertFalse(market_relative_underperformance_gate(-0.5, threshold=-0.5))

    def test_blocks_just_below_threshold_boundary(self) -> None:
        self.assertTrue(market_relative_underperformance_gate(-0.5001, threshold=-0.5))

    def test_none_alpha_does_not_block(self) -> None:
        self.assertFalse(market_relative_underperformance_gate(None, threshold=-0.5))

    def test_zero_alpha_does_not_block(self) -> None:
        self.assertFalse(market_relative_underperformance_gate(0.0, threshold=-0.5))


class TestEvaluateEntryBlocks(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()
        reset_session_monitor()

    def test_blocks_when_alpha_below_threshold(self) -> None:
        result = evaluate_entry_blocks({"alpha_vs_spy_pct": -0.70, "benchmark_ok": True})
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "market_relative_underperformance")
        self.assertTrue(result["market_relative_underperformance"])
        self.assertIn("market_relative_underperformance", result.get("entry_block_breakdown") or {})
        self.assertIn("market_relative", result.get("entry_block_breakdown") or {})

    def test_allows_when_alpha_above_threshold(self) -> None:
        result = evaluate_entry_blocks({"alpha_vs_spy_pct": -0.3, "benchmark_ok": True})
        self.assertFalse(result["blocked"])

    def test_skips_when_benchmark_unavailable(self) -> None:
        result = evaluate_entry_blocks({"alpha_vs_spy_pct": -2.0, "benchmark_ok": False})
        self.assertFalse(result["blocked"])

    def test_skips_when_alpha_missing(self) -> None:
        result = evaluate_entry_blocks({"benchmark_ok": True})
        self.assertFalse(result["blocked"])

    def test_blocks_si_plan_scenario_alpha_minus_0_64_spy_plus_0_64(self) -> None:
        result = evaluate_entry_blocks(
            {
                "session_return_pct": 0.0,
                "benchmark_change_1d_pct": 0.64,
                "benchmark_ok": True,
            }
        )
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "market_relative_underperformance")

    def test_allows_when_guard_disabled(self) -> None:
        with patch(
            "utils.portfolio_session.entry_guards.get_market_relative_underperformance_enabled",
            return_value=False,
        ):
            result = evaluate_entry_blocks({"alpha_vs_spy_pct": -2.0, "benchmark_ok": True})
        self.assertFalse(result["blocked"])


class TestSessionStateAlphaPersistence(unittest.TestCase):
    def test_alpha_vs_spy_method(self) -> None:
        state = SessionState(
            {
                "session_return_pct": 0.0,
                "benchmark_change_1d_pct": 0.64,
                "benchmark_ok": True,
            }
        )
        self.assertAlmostEqual(state.alpha_vs_spy(), -0.64)

    def test_update_persists_session_alpha_vs_spy(self) -> None:
        state = SessionState(
            {
                "session_return_pct": 0.0,
                "benchmark_change_1d_pct": 0.64,
                "benchmark_ok": True,
            }
        ).update()
        self.assertAlmostEqual(state["session_alpha_vs_spy"], -0.64)
        self.assertAlmostEqual(state["alpha_vs_spy_pct"], -0.64)

    def test_default_threshold_is_minus_half_percent(self) -> None:
        self.assertEqual(get_market_relative_underperformance_threshold(), -0.5)


if __name__ == "__main__":
    unittest.main()
