"""Per-symbol adaptive policy tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.adaptive_policy import apply_adaptations, reset_session_adaptive_state


class TestSkimAdaptivePolicy(unittest.TestCase):
    def test_auto_pause_short_on_toxic_side(self):
        learned = {
            "params": {"pause_short": False, "pattern_deltas": {}},
            "session_stats": {
                "exits": 12,
                "wins": 4,
                "losses": 8,
                "sum_pnl_usd": -1.2,
                "long_exits": 2,
                "long_pnl_usd": 0.1,
                "long_wins": 1,
                "long_losses": 1,
                "short_exits": 10,
                "short_pnl_usd": -1.3,
                "short_wins": 3,
                "short_losses": 7,
            },
            "pattern_stats": {},
            "causation": {"keys": {}, "eliminated_keys": [], "lifetime_exits": 0},
        }
        with tempfile.TemporaryDirectory() as td:
            notes = apply_adaptations("NVDA", learned, experience_path_fn=lambda s: Path(td) / f"{s}.jsonl")
        self.assertTrue(learned["params"]["pause_short"])
        self.assertTrue(any("auto_pause_short" in n for n in notes))

    def test_auto_pause_entries_on_bleeder(self):
        learned = {
            "params": {"pause_entries": False, "pattern_deltas": {}},
            "session_stats": {
                "exits": 20,
                "wins": 5,
                "losses": 15,
                "sum_pnl_usd": -5.5,
                "long_exits": 10,
                "long_pnl_usd": -3.0,
                "long_wins": 2,
                "long_losses": 8,
                "short_exits": 10,
                "short_pnl_usd": -2.5,
                "short_wins": 3,
                "short_losses": 7,
            },
            "pattern_stats": {},
            "causation": {"keys": {}, "eliminated_keys": [], "lifetime_exits": 0},
        }
        notes = apply_adaptations("LLY", learned, experience_path_fn=lambda s: Path("/dev/null"))
        self.assertTrue(learned["params"]["pause_entries"])
        self.assertTrue(any("auto_pause_entries" in n for n in notes))

    def test_session_reset_clears_pauses(self):
        learned = {
            "params": {"pause_long": True, "pause_short": True, "pause_entries": True},
            "causation": {
                "keys": {"k1": {"eliminated": True}},
                "eliminated_keys": ["k1"],
                "lifetime_exits": 1,
            },
        }
        reset_session_adaptive_state(learned)
        self.assertFalse(learned["params"]["pause_long"])
        self.assertFalse(learned["params"]["pause_short"])
        self.assertFalse(learned["params"]["pause_entries"])
        self.assertEqual(learned["causation"]["eliminated_keys"], [])

    def test_auto_disable_toxic_pattern(self):
        learned = {
            "params": {"disable_patterns": [], "pattern_deltas": {}},
            "session_stats": {"exits": 8, "wins": 3, "losses": 5, "sum_pnl_usd": -1.0},
            "pattern_stats": {
                "rip_fade": {"exits": 5, "wins": 1, "sum_pnl_usd": -0.80},
            },
            "causation": {"keys": {}, "eliminated_keys": [], "lifetime_exits": 0},
        }
        notes = apply_adaptations("SPY", learned, experience_path_fn=lambda s: Path("/dev/null"))
        self.assertIn("rip_fade", learned["params"]["disable_patterns"])
        self.assertTrue(any("auto_disable_rip_fade" in n for n in notes))

    def test_disable_loser_for_winning_pattern_share(self):
        learned = {
            "params": {"disable_patterns": [], "pattern_deltas": {}},
            "session_stats": {"exits": 12, "wins": 6, "losses": 6, "sum_pnl_usd": 0.5},
            "pattern_stats": {
                "rip_fade": {"exits": 4, "wins": 2, "sum_pnl_usd": -0.05},
                "pullback_uptrend": {"exits": 4, "wins": 3, "sum_pnl_usd": 0.80},
                "momentum_long": {"exits": 4, "wins": 1, "sum_pnl_usd": -0.08},
            },
            "causation": {"keys": {}, "eliminated_keys": [], "lifetime_exits": 0},
        }
        with patch("agents.skim_swarm.adaptive_policy.target_winning_pattern_share", return_value=0.75):
            notes = apply_adaptations("NVDA", learned, experience_path_fn=lambda s: Path("/dev/null"))
        self.assertTrue(len(learned["params"]["disable_patterns"]) >= 1)
        self.assertTrue(any("disable_loser_for_pattern_share" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
