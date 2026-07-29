"""Unit tests for portfolio session pre_trade_gate (SI fix)."""
from __future__ import annotations

import unittest

from utils.portfolio_session.gates.base import GateResult
from utils.portfolio_session.pre_trade_gate import check_market_relative_underperformance
from utils.portfolio_session.session_state import SessionState


class TestPreTradeGateMarketRelativeUnderperformance(unittest.TestCase):
    def test_blocks_when_alpha_below_threshold(self) -> None:
        result = check_market_relative_underperformance(
            {"alpha_vs_spy_pct": -0.6, "benchmark_ok": True},
            {"market_relative_underperformance_threshold": -0.5},
        )
        self.assertIsInstance(result, GateResult)
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_allows_when_alpha_above_threshold(self) -> None:
        result = check_market_relative_underperformance(
            SessionState({"alpha_vs_spy_pct": -0.3, "benchmark_ok": True}),
            {"market_relative_underperformance_threshold": -0.5},
        )
        self.assertFalse(result.blocked)

    def test_session_state_alpha_vs_spy_method(self) -> None:
        tracker = SessionState(
            {
                "session_return_pct": 0.0,
                "spy_change_pct": 0.64,
                "benchmark_ok": True,
            }
        )
        self.assertAlmostEqual(tracker.alpha_vs_spy(), -0.64)
        self.assertAlmostEqual(tracker.spy_change_pct, 0.64)


if __name__ == "__main__":
    unittest.main()
