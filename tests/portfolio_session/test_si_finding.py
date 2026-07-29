"""Unit tests for portfolio_session si_finding schema."""
from __future__ import annotations

import unittest

from utils.portfolio_session.si_finding import (
    ENTRY_BLOCK_BREAKDOWN_KEYS,
    build_market_relative_finding_detail,
    normalize_entry_block_breakdown,
)


class TestSiFindingSchema(unittest.TestCase):
    def test_normalize_entry_block_breakdown_includes_market_relative(self) -> None:
        breakdown = normalize_entry_block_breakdown({"denylist": 2})
        for key in ENTRY_BLOCK_BREAKDOWN_KEYS:
            self.assertIn(key, breakdown)
        self.assertEqual(breakdown["denylist"], 2)
        self.assertEqual(breakdown["market_relative"], 0)

    def test_build_market_relative_finding_detail(self) -> None:
        detail = build_market_relative_finding_detail(
            alpha_vs_spy_pct=-0.92,
            benchmark_change_1d_pct=0.92,
            session_realized_usd=0.0,
            session_exit_count=0,
            entry_block_breakdown={"market_relative": 3},
        )
        self.assertEqual(detail["entry_block_breakdown"]["market_relative"], 3)
        self.assertEqual(detail["alpha_vs_spy_pct"], -0.92)


if __name__ == "__main__":
    unittest.main()
