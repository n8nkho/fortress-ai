"""Tests for SI-gated skim clip ladder."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.signal import decide


class TestSkimClipLadder(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name
        os.environ["FORTRESS_SKIM_CLIP_LADDER"] = "1"
        os.environ["FORTRESS_SKIM_MAX_SHARES_PER_SYMBOL"] = "5"
        os.environ["FORTRESS_SKIM_CLIP_SIZE"] = "1"
        os.environ["FORTRESS_SKIM_CLIP_MIN_GAP_SEC"] = "30"
        os.environ["FORTRESS_SKIM_CLIP_MIN_HOLD_SEC"] = "10"
        # session_date_et is bound into multiple modules at import time; patch each so
        # load_learned / load_symbol_state do not reset stats on a different run date.
        for target in (
            "agents.skim_swarm.eod.session_date_et",
            "agents.skim_swarm.symbol_learning.session_date_et",
            "agents.skim_swarm.state.session_date_et",
        ):
            p = patch(target, return_value="2026-05-27")
            p.start()
            self.addCleanup(p.stop)

    def _write_learned(self, symbol: str, *, exits: int, wins: int, losses: int, pnl: float):
        learned_dir = Path(self._td.name) / "skim_swarm" / "learned"
        learned_dir.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": 5,
            "session_date_et": "2026-05-27",
            "params": {"pattern_deltas": {}},
            "session_stats": {
                "exits": exits,
                "wins": wins,
                "losses": losses,
                "sum_pnl_usd": pnl,
            },
        }
        (learned_dir / f"{symbol}.json").write_text(json.dumps(doc), encoding="utf-8")

    def test_partial_exit_at_target_when_multi_share(self):
        features = {
            "symbol": "SPY",
            "last": 500.0,
            "r1m": 0.0,
            "r5m": 0.0,
            "side": "long",
            "position_qty": 3,
            "unrealized_usd": 0.20,
            "thin_etf": False,
        }
        st = {"side": "long", "peak_unrealized": 0.20}
        params = {
            "enter_long": 0.05,
            "enter_short": -0.05,
            "target_mult": 1.0,
            "target_mult_effective": 1.0,
            "stop_target_mult_effective": 0.7,
            "spread_bps_mult": 1.0,
            "pause_entries": False,
            "pattern_deltas": {},
            "disable_patterns": [],
            "score_bias": 0,
            "cooldown_mult": 1,
        }
        with patch("agents.skim_swarm.signal.get_params", return_value=params):
            with patch("agents.skim_swarm.signal.adaptive_target_usd", return_value=0.12):
                with patch("agents.skim_swarm.signal.is_force_flatten_window", return_value=False):
                    with patch("agents.skim_swarm.signal.describe_eod_phase", return_value="normal"):
                        d = decide(features, st, swarm_halted=False, open_positions=1, max_open=6)
        self.assertEqual(d["action"], "exit_partial")
        self.assertEqual(d.get("exit_qty"), 1)

    def test_add_clip_blocked_when_unrealized_negative(self):
        from utils.skim_clip_ladder import authorize_add_clip
        from utils.swarm_session_si import save_session_policy

        self._write_learned("SPY", exits=5, wins=4, losses=1, pnl=1.0)
        save_session_policy("skim_swarm", {"mode": "normal"})
        ok, reason = authorize_add_clip(
            "SPY",
            side="long",
            pos_qty=1,
            unrealized=-0.05,
            score=0.5,
            enter_threshold=0.2,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "clip_unrealized_negative")

    def test_effective_max_shares_respects_session_critical(self):
        from utils.swarm_session_si import save_session_policy
        from utils.skim_clip_ladder import effective_max_shares

        self._write_learned("SPY", exits=5, wins=4, losses=1, pnl=1.0)
        save_session_policy(
            "skim_swarm",
            {"mode": "critical", "max_open_effective": 2},
        )
        self.assertEqual(effective_max_shares("SPY"), 1)

    def test_add_clip_authorized_after_hold_window(self):
        from utils.skim_clip_ladder import authorize_add_clip
        from utils.swarm_session_si import save_session_policy

        self._write_learned("AAPL", exits=5, wins=4, losses=1, pnl=1.2)
        save_session_policy("skim_swarm", {"mode": "normal"})
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        state_dir = Path(self._td.name) / "skim_swarm" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "AAPL.json").write_text(
            json.dumps({"session_date_et": "2026-05-27", "entry_ts": old, "last_clip_ts": old}),
            encoding="utf-8",
        )
        with patch("agents.skim_swarm.eod.session_date_et", return_value="2026-05-27"):
            ok, reason = authorize_add_clip(
                "AAPL",
                side="long",
                pos_qty=1,
                unrealized=0.08,
                score=0.35,
                enter_threshold=0.22,
            )
        self.assertTrue(ok, reason)

    def test_ladder_off_behaves_like_one_share(self):
        os.environ["FORTRESS_SKIM_CLIP_LADDER"] = "0"
        from utils.skim_clip_ladder import effective_max_shares

        self.assertEqual(effective_max_shares("SPY"), 1)


if __name__ == "__main__":
    unittest.main()
