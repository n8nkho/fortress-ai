"""Unit tests for market_relative_underperformance guard (SI plan thresholds)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

FORTRESS = Path(__file__).resolve().parents[1]
PATCH_ROOT = FORTRESS / "deploy" / "trading-bot-patches"
for root in (FORTRESS, PATCH_ROOT):
    text = str(root)
    if root.is_dir() and text not in sys.path:
        sys.path.insert(0, text)

from utils.portfolio_session.guards.market_relative_underperformance import (  # noqa: E402
    MarketRelativeUnderperformanceGuard,
)


class TestMarketRelativeUnderperformanceGuard(unittest.TestCase):
    def test_blocks_when_alpha_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5}
        )
        result = guard.check_session_context(
            {"alpha_vs_spy_pct": -0.6, "benchmark_ok": True}
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")
        self.assertIn("session_underperforming", result.detail)

    def test_allows_when_alpha_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5}
        )
        result = guard.check_session_context(
            {"alpha_vs_spy_pct": -0.4, "benchmark_ok": True}
        )
        self.assertFalse(result.blocked)

    def test_exact_threshold_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5}
        )
        result = guard.check_session_context(
            {"alpha_vs_spy_pct": -0.5, "benchmark_ok": True}
        )
        self.assertFalse(result.blocked)

    def test_benchmark_unavailable_does_not_block(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5}
        )
        result = guard.check_session_context(
            {"alpha_vs_spy_pct": -1.0, "benchmark_ok": False}
        )
        self.assertFalse(result.blocked)

    def test_should_block_entry_returns_reason_for_session_metrics(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5}
        )
        reason = guard.should_block_entry({"alpha_vs_spy_pct": -0.6, "benchmark_ok": True})
        self.assertEqual(reason, "market_relative_underperformance")

    def test_should_block_entry_returns_none_when_above_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard(
            config={"underperformance_threshold_pct": -0.5}
        )
        self.assertIsNone(
            guard.should_block_entry({"alpha_vs_spy_pct": -0.4, "benchmark_ok": True})
        )


class TestSrcEntryManager(unittest.TestCase):
    def test_entry_manager_blocks_via_guard_registry(self) -> None:
        from src.entry_manager import evaluate_entry  # noqa: E402

        decision = evaluate_entry(
            {
                "alpha_vs_spy_pct": -0.6,
                "benchmark_ok": True,
                "component": "portfolio_session",
            }
        )
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "market_relative_underperformance")

    def test_entry_manager_allows_above_threshold(self) -> None:
        from src.entry_manager import evaluate_entry  # noqa: E402

        decision = evaluate_entry(
            {
                "alpha_vs_spy_pct": -0.4,
                "benchmark_ok": True,
            }
        )
        self.assertFalse(decision.blocked)

    def test_entry_manager_skips_when_order_specific_blocks_active(self) -> None:
        from utils.portfolio_session.entry_manager import evaluate_entry  # noqa: E402

        decision, _state = evaluate_entry(
            {
                "alpha_vs_spy_pct": -2.0,
                "benchmark_ok": True,
            },
            prior_block_reason="pause_entries",
        )
        self.assertFalse(decision.blocked)

    def test_check_guard_blocks_via_registry(self) -> None:
        from utils.portfolio_session.entry_manager import check_guard  # noqa: E402

        decision = check_guard(
            "market_relative_underperformance",
            {"alpha_vs_spy_pct": -0.6, "benchmark_ok": True},
        )
        self.assertTrue(decision.blocked)
        self.assertEqual(decision.reason, "market_relative_underperformance")


if __name__ == "__main__":
    unittest.main()
