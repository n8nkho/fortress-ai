"""Unit tests for portfolio session entry_guard_router."""
from __future__ import annotations

import unittest

from utils.portfolio_session.entry_guard_router import (
    GUARD_CHAIN,
    build_entry_guards,
    evaluate_guard_chain,
)
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
)
from utils.portfolio_session.risk_manager import reset_market_relative_cooldown


class TestEntryGuardRouter(unittest.TestCase):
    def setUp(self) -> None:
        reset_market_relative_cooldown()
        MarketRelativeUnderperformanceGuard.reset_cooldown()

    def test_guard_chain_order(self) -> None:
        self.assertEqual(
            GUARD_CHAIN,
            ("market_relative", "market_relative_underperformance"),
        )

    def test_build_entry_guards_includes_underperformance_when_enabled(self) -> None:
        guards = build_entry_guards({"enabled": True, "threshold_pct": -0.5})
        names = [getattr(g, "name", "") for g in guards]
        self.assertIn("market_relative_underperformance", names)

    def test_evaluate_guard_chain_blocks_below_threshold(self) -> None:
        result = evaluate_guard_chain(
            {"alpha_vs_spy_pct": -0.7, "benchmark_ok": True},
            config={"enabled": True, "threshold_pct": -0.5},
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_evaluate_guard_chain_passes_above_threshold(self) -> None:
        result = evaluate_guard_chain(
            {"alpha_vs_spy_pct": -0.3, "benchmark_ok": True},
            config={"enabled": True, "threshold_pct": -0.5},
        )
        self.assertFalse(result.blocked)


if __name__ == "__main__":
    unittest.main()
