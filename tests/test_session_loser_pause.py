"""Tests for session loser auto-pause."""
from __future__ import annotations

import os
import unittest


class TestSessionLoserPause(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_SESSION_LOSER_PAUSE"] = "1"
        os.environ["FORTRESS_SKIM_SESSION_LOSER_MIN_LOSSES"] = "4"
        os.environ["FORTRESS_SKIM_SESSION_LOSER_MIN_PNL_USD"] = "-0.25"

    def test_pause_on_session_losses(self):
        from utils.session_loser_pause import should_pause_session_loser

        pause, reason = should_pause_session_loser(
            {"exits": 7, "wins": 2, "losses": 5, "sum_pnl_usd": -0.38},
            component="skim_swarm",
        )
        self.assertTrue(pause)
        self.assertIn("session_loser", reason)

    def test_no_pause_small_loss(self):
        from utils.session_loser_pause import should_pause_session_loser

        pause, _ = should_pause_session_loser(
            {"exits": 3, "wins": 2, "losses": 1, "sum_pnl_usd": -0.10},
            component="skim_swarm",
        )
        self.assertFalse(pause)

    def test_apply_params_sets_pause_entries(self):
        from utils.session_loser_pause import apply_session_loser_pause_to_params

        params: dict = {}
        note = apply_session_loser_pause_to_params(
            params,
            {"exits": 6, "wins": 1, "losses": 5, "sum_pnl_usd": -0.40},
            component="skim_swarm",
        )
        self.assertTrue(params.get("pause_entries"))
        self.assertIsNotNone(note)


if __name__ == "__main__":
    unittest.main()
