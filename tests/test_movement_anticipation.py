"""Tests for movement anticipation pre-execution context."""
from __future__ import annotations

import unittest

from utils.movement_anticipation import (
    compute_movement_anticipation,
    entry_blocked_by_anticipation,
    enrich_features_with_anticipation,
)


class TestMovementAnticipation(unittest.TestCase):
    def test_continuation_bias_up(self):
        ant = compute_movement_anticipation(
            {
                "symbol": "MSFT",
                "r1m": 0.0012,
                "r3m": 0.0025,
                "r5m": 0.0015,
                "rsi1m": 58,
                "spy_r5m": 0.0005,
                "residual_vs_spy": 0.0002,
            },
            promoted_hypotheses=set(),
        )
        self.assertTrue(ant["enabled"])
        self.assertEqual(ant["regime"], "continuation")
        self.assertGreater(ant["bias"], 0)
        self.assertGreater(ant["confidence"], 0.4)

    def test_chop_blocks_when_promoted(self):
        ant = compute_movement_anticipation(
            {
                "symbol": "SPY",
                "r1m": 0.0001,
                "r3m": 0.0002,
                "r5m": 0.0001,
                "rsi1m": 50,
                "spy_r5m": 0.0001,
            },
            promoted_hypotheses={"chop_no_edge"},
        )
        self.assertEqual(ant["regime"], "chop")
        self.assertTrue(ant["block_long"])
        self.assertTrue(ant["block_short"])
        blocked, reason = entry_blocked_by_anticipation("long", ant)
        self.assertTrue(blocked)
        self.assertIn("chop", reason or "")

    def test_countertrend_block_when_promoted(self):
        ant = compute_movement_anticipation(
            {
                "symbol": "NVDA",
                "r1m": 0.0010,
                "r3m": 0.0020,
                "r5m": 0.0012,
                "rsi1m": 62,
                "spy_r5m": 0.0004,
            },
            promoted_hypotheses={"block_countertrend_entries"},
        )
        self.assertEqual(ant["regime"], "continuation")
        self.assertTrue(ant["block_short"])
        self.assertFalse(ant["block_long"])

    def test_enrich_attaches_to_features(self):
        feats = {"symbol": "AAPL", "r1m": 0.0, "r3m": 0.0, "r5m": 0.0, "rsi1m": 50}
        enrich_features_with_anticipation(feats, component="skim_swarm")
        self.assertIn("movement_anticipation", feats)
        self.assertTrue(feats["movement_anticipation"]["enabled"])


if __name__ == "__main__":
    unittest.main()
