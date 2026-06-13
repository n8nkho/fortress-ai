"""Consciousness posture + proactive SI + session diary."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.consciousness_posture import (
    apply_entry_threshold_delta,
    compute_consciousness_posture,
    proactive_si_trigger,
)
from utils.session_diary import record_swarm_event, session_diary_summary


class TestConsciousnessPosture(unittest.TestCase):
    def test_participation_boost_on_strong_tape_gap(self):
        mc = {
            "temporal": {"rth_active": True, "slot_key": "Fri-14"},
            "historical_hour_profile": {"SPY": {"mean_return_pct": 0.04, "win_rate_long": 0.55}},
            "market_tape": {"strong_tape_1d": True, "change_1d_pct": 0.5},
            "self_state": {"alpha_vs_spy_pct": -0.6, "session_exit_count": 2},
        }
        p = compute_consciousness_posture(mc, {"vix_last": 16})
        self.assertEqual(p["mode"], "participation_boost")
        self.assertLess(p["entry_threshold_delta"], 0)
        self.assertGreater(p["score_delta"], 0)

    def test_defensive_tighten_high_vix(self):
        mc = {
            "temporal": {"rth_active": True},
            "historical_hour_profile": {"SPY": {"mean_return_pct": -0.08, "win_rate_long": 0.4}},
            "market_tape": {},
            "self_state": {},
        }
        p = compute_consciousness_posture(mc, {"vix_last": 30})
        self.assertGreater(p["entry_threshold_delta"], 0)

    def test_entry_threshold_delta_bounded(self):
        el, es = apply_entry_threshold_delta(0.45, -0.45, {"enabled": True, "mode": "participation_boost", "entry_threshold_delta": -0.05})
        self.assertLess(el, 0.45)

    def test_proactive_si_trigger(self):
        mc = {
            "enabled": True,
            "market_tape": {"strong_tape_1d": True},
            "self_state": {"alpha_vs_spy_pct": -0.5, "session_exit_count": 1},
        }
        t = proactive_si_trigger(mc)
        self.assertTrue(t.get("triggered"))


class TestSessionDiary(unittest.TestCase):
    def test_record_and_summary(self):
        with tempfile.TemporaryDirectory() as td:
            diary = Path(td) / "session_diary.jsonl"
            with patch("utils.session_diary.diary_path", return_value=diary):
                with patch("utils.session_diary._session_date_et", return_value="2026-06-12"):
                    with patch("utils.market_consciousness.current_temporal_slot", return_value={"slot_key": "Fri-14"}):
                        record_swarm_event(
                            component="skim_swarm",
                            symbol="SPY",
                            decision={"action": "enter_long", "reasoning": "test", "score": 0.5},
                            act_result={"executed": True},
                            features={},
                        )
                        s = session_diary_summary(session_date="2026-06-12")
            self.assertEqual(s.get("entries_executed"), 1)


if __name__ == "__main__":
    unittest.main()
