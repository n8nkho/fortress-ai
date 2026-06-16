"""Tests for winning-pattern admission gate."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestWinningPatternGate(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_WINNING_PATTERN_GATE"] = "1"

    def test_blocks_lifetime_loser(self):
        from utils.winning_pattern_gate import winning_pattern_entry_blocked

        with patch(
            "utils.winning_pattern_gate.pattern_lifetime_expectancy",
            return_value=(-0.05, 8),
        ):
            blocked, reason = winning_pattern_entry_blocked("rip_fade")
        self.assertTrue(blocked)
        self.assertIn("lifetime_pattern_negative", reason)

    def test_allows_unknown_pattern(self):
        from utils.winning_pattern_gate import winning_pattern_entry_blocked

        with patch("utils.winning_pattern_gate.pattern_lifetime_expectancy", return_value=(None, 1)):
            blocked, _ = winning_pattern_entry_blocked("momentum_long")
        self.assertFalse(blocked)


if __name__ == "__main__":
    unittest.main()
