"""Unit tests for fortress_ai market_relative_underperformance guard (SI plan)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

FORTRESS = Path(__file__).resolve().parents[1]
PATCH_ROOT = FORTRESS / "deploy" / "trading-bot-patches"
for root in (str(PATCH_ROOT), str(FORTRESS)):
    if root not in sys.path:
        sys.path.insert(0, root)

from fortress_ai.guards import (  # noqa: E402
    GUARD_REGISTRY,
    MarketRelativeUnderperformanceGuard,
)
from fortress_ai.portfolio_session import evaluate_entry_blocks  # noqa: E402


class TestFortressAiMarketRelativeGuard(unittest.TestCase):
    def test_guard_registry_contains_underperformance_guard(self) -> None:
        self.assertIn("market_relative_underperformance", GUARD_REGISTRY)

    def test_should_block_below_threshold(self) -> None:
        guard = MarketRelativeUnderperformanceGuard({"underperformance_threshold_pct": -0.5})
        self.assertTrue(guard.should_block(-0.6))
        self.assertIn("session_underperforming", guard.reason())

    def test_evaluate_entry_blocks_si_plan(self) -> None:
        blocked, reason, state = evaluate_entry_blocks(
            {"alpha_vs_spy_pct": -0.5169, "benchmark_ok": True}
        )
        self.assertTrue(blocked)
        self.assertEqual(reason, "market_relative_underperformance")
        self.assertEqual(state["entry_block_breakdown"]["market_relative"], 1)


if __name__ == "__main__":
    unittest.main()
