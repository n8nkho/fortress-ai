"""Unit tests for portfolio_session entry_blocks — market_relative_underperformance."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.portfolio_session.entry_blocks import (
    ENTRY_BLOCK_REGISTRY,
    evaluate_entry_blocks,
    market_relative_underperformance_block,
)


class TestMarketRelativeUnderperformanceBlock(unittest.TestCase):
    def test_blocks_when_alpha_below_threshold(self) -> None:
        self.assertTrue(market_relative_underperformance_block(-0.6, -0.005))
        self.assertTrue(market_relative_underperformance_block(-2.0, -1.5))
        self.assertTrue(market_relative_underperformance_block(-1.51, -1.5))

    def test_no_block_when_alpha_at_or_above_threshold(self) -> None:
        self.assertFalse(market_relative_underperformance_block(-0.5, -0.005))
        self.assertFalse(market_relative_underperformance_block(-0.4, -0.005))
        self.assertFalse(market_relative_underperformance_block(-1.5, -1.5))
        self.assertFalse(market_relative_underperformance_block(-1.0, -1.5))
        self.assertFalse(market_relative_underperformance_block(0.5, -1.5))

    def test_threshold_zero_blocks_any_negative_alpha(self) -> None:
        self.assertTrue(market_relative_underperformance_block(-0.01, 0.0))
        self.assertFalse(market_relative_underperformance_block(0.0, 0.0))

    def test_extreme_negative_alpha_blocks(self) -> None:
        self.assertTrue(market_relative_underperformance_block(-50.0, -1.5))

    def test_registry_contains_market_relative_block(self) -> None:
        self.assertIn("market_relative_underperformance", ENTRY_BLOCK_REGISTRY)
        block_fn = ENTRY_BLOCK_REGISTRY["market_relative_underperformance"]
        self.assertTrue(block_fn(-2.0, -1.5))
        self.assertFalse(block_fn(-1.0, -1.5))


class TestEvaluateEntryBlocks(unittest.TestCase):
    @patch("utils.portfolio_session.entry_blocks.get_market_relative_underperformance_enabled", return_value=True)
    @patch(
        "utils.portfolio_session.entry_blocks.get_market_relative_entry_block_config",
        return_value={"enabled": True, "threshold": -0.005},
    )
    def test_blocks_when_alpha_below_configured_threshold(self, _cfg, _enabled) -> None:
        blocked, reason, state = evaluate_entry_blocks(
            {"alpha_vs_spy_pct": -0.5169, "benchmark_ok": True},
            session_alpha_vs_spy=-0.5169,
        )
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")
        self.assertEqual(state["entry_block_breakdown"]["market_relative"], 1)

    @patch("utils.portfolio_session.entry_blocks.get_market_relative_underperformance_enabled", return_value=True)
    @patch(
        "utils.portfolio_session.entry_blocks.get_market_relative_entry_block_config",
        return_value={"enabled": True, "threshold": -0.005},
    )
    def test_allows_when_alpha_at_threshold(self, _cfg, _enabled) -> None:
        blocked, reason, _state = evaluate_entry_blocks(
            {"alpha_vs_spy_pct": -0.5, "benchmark_ok": True},
            session_alpha_vs_spy=-0.5,
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    @patch("utils.portfolio_session.entry_blocks.get_market_relative_underperformance_enabled", return_value=True)
    @patch(
        "utils.portfolio_session.entry_blocks.get_market_relative_entry_block_config",
        return_value={"enabled": True, "threshold": -0.005},
    )
    def test_allows_when_alpha_above_threshold(self, _cfg, _enabled) -> None:
        blocked, reason, _state = evaluate_entry_blocks(
            {"alpha_vs_spy_pct": -0.4, "benchmark_ok": True},
            session_alpha_vs_spy=-0.4,
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    @patch("utils.portfolio_session.entry_blocks.get_market_relative_underperformance_enabled", return_value=True)
    @patch(
        "utils.portfolio_session.entry_blocks.get_market_relative_entry_block_config",
        return_value={"enabled": True, "threshold": -0.005},
    )
    def test_allows_si_plan_mild_underperformance(self, _cfg, _enabled) -> None:
        """Alpha -0.38pp vs -0.5pp threshold passes (constructive tape scenario)."""
        blocked, reason, _state = evaluate_entry_blocks(
            {"alpha_vs_spy_pct": -0.38, "benchmark_ok": True},
            session_alpha_vs_spy=-0.38,
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
