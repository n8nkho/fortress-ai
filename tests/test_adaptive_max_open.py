"""Tests for adaptive max-open scaling."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestAdaptiveMaxOpen(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name
        os.environ["FORTRESS_SKIM_MAX_OPEN_POSITIONS"] = "4"
        os.environ["FORTRESS_ADAPTIVE_MAX_OPEN"] = "1"
        os.environ["FORTRESS_SWARM_MAX_OPEN_CEILING"] = "10"
        os.environ["FORTRESS_SWARM_MAX_OPEN_AGGRESSIVE"] = "20"

    def test_base_when_no_signals(self):
        from utils.adaptive_max_open import compute_adaptive_max_open

        with patch("utils.adaptive_max_open._consciousness_bundle", return_value={"enabled": False}):
            doc = compute_adaptive_max_open("skim_swarm")
        self.assertEqual(doc["effective"], 4)
        self.assertEqual(doc["boost"], 0)

    def test_strong_tape_boosts_toward_ceiling(self):
        from utils.adaptive_max_open import compute_adaptive_max_open

        mc = {
            "enabled": True,
            "temporal": {"rth_active": True},
            "market_tape": {"strong_tape_1d": True, "change_1d_pct": 1.2},
            "self_state": {"alpha_vs_spy_pct": -0.5, "session_exit_count": 2},
            "consciousness_posture": {"mode": "participation_boost"},
            "session_intent": {"participation_target": 0.65},
        }
        doc = compute_adaptive_max_open("skim_swarm", consciousness=mc)
        self.assertGreaterEqual(doc["effective"], 10)
        self.assertIn("participation_boost", doc["markers"])

    def test_aggressive_ceiling_when_stacked(self):
        from utils.adaptive_max_open import compute_adaptive_max_open

        mc = {
            "enabled": True,
            "temporal": {"rth_active": True},
            "market_tape": {"strong_tape_1d": True, "change_1d_pct": 1.5, "spy_change_1d_pct": 1.0},
            "self_state": {"alpha_vs_spy_pct": -1.0, "session_exit_count": 1},
            "consciousness_posture": {"mode": "participation_boost"},
            "session_intent": {"participation_target": 0.7},
        }
        with patch("utils.swarm_session_si.load_session_policy", return_value={"session_expectancy_usd": 0.05, "session_win_rate": 0.55}):
            doc = compute_adaptive_max_open("skim_swarm", consciousness=mc)
        self.assertGreaterEqual(doc["effective"], 15)
        self.assertLessEqual(doc["effective"], 20)

    def test_defensive_tighten_reduces_boost(self):
        from utils.adaptive_max_open import compute_adaptive_max_open

        mc = {
            "enabled": True,
            "temporal": {"rth_active": True},
            "market_tape": {"strong_tape_1d": True, "change_1d_pct": 0.4},
            "self_state": {"alpha_vs_spy_pct": 0.1, "session_exit_count": 5},
            "consciousness_posture": {"mode": "defensive_tighten"},
        }
        doc = compute_adaptive_max_open("skim_swarm", consciousness=mc)
        self.assertLessEqual(doc["effective"], 6)

    def test_effective_max_open_respects_session_tighten(self):
        from utils.swarm_session_si import effective_max_open, save_session_policy

        mc = {
            "enabled": True,
            "temporal": {"rth_active": True},
            "market_tape": {"strong_tape_1d": True, "change_1d_pct": 1.0},
            "self_state": {"alpha_vs_spy_pct": -0.4, "session_exit_count": 2},
            "consciousness_posture": {"mode": "participation_boost"},
        }
        with patch("utils.adaptive_max_open._consciousness_bundle", return_value=mc):
            adaptive = effective_max_open("skim_swarm")
        self.assertGreaterEqual(adaptive, 8)
        save_session_policy("skim_swarm", {"mode": "tight", "max_open_effective": 5})
        with patch("utils.adaptive_max_open._consciousness_bundle", return_value=mc):
            self.assertEqual(effective_max_open("skim_swarm"), 5)


if __name__ == "__main__":
    unittest.main()
