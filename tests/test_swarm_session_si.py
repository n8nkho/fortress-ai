"""Tests for swarm-level session SI (negative edge / over-churn)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestSwarmSessionSI(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name
        os.environ["FORTRESS_SKIM_SWARM_SESSION_SI"] = "1"
        os.environ["FORTRESS_SKIM_CHURN_MAX_EXITS_SESSION"] = "22"
        os.environ["FORTRESS_SKIM_CHURN_MIN_WIN_RATE"] = "0.38"
        os.environ["FORTRESS_SKIM_CHURN_MIN_EXITS"] = "8"
        os.environ["FORTRESS_SKIM_SESSION_EXPECTANCY_MIN_USD"] = "-0.05"
        os.environ["FORTRESS_SKIM_MAX_OPEN_POSITIONS"] = "6"

    def _write_learned(self, component: str, symbol: str, *, exits: int, wins: int, losses: int, pnl: float):
        learned = Path(self._td.name) / "skim_swarm" / "learned"
        learned.mkdir(parents=True, exist_ok=True)
        doc = {
            "session_date_et": "2026-05-27",
            "session_stats": {
                "exits": exits,
                "wins": wins,
                "losses": losses,
                "sum_pnl_usd": pnl,
                "entries": exits,
                "decisions": exits * 2,
            },
        }
        (learned / f"{symbol}.json").write_text(json.dumps(doc), encoding="utf-8")

    def test_normal_session_no_tightening(self):
        from utils.swarm_session_si import adapt_swarm_session, effective_max_open

        self._write_learned("skim_swarm", "SPY", exits=5, wins=3, losses=2, pnl=0.40)
        with patch("utils.swarm_session_si._session_date", return_value="2026-05-27"):
            pol = adapt_swarm_session("skim_swarm")
        self.assertEqual(pol["mode"], "normal")
        self.assertEqual(effective_max_open("skim_swarm"), 6)

    def test_negative_edge_tightens(self):
        from utils.swarm_session_si import adapt_swarm_session, effective_max_open, session_entry_boosts

        self._write_learned("skim_swarm", "AAPL", exits=10, wins=3, losses=7, pnl=-2.50)
        with patch("utils.swarm_session_si._session_date", return_value="2026-05-27"):
            pol = adapt_swarm_session("skim_swarm", day_realized_pnl=-2.50)
        self.assertTrue(pol["negative_edge"])
        self.assertEqual(pol["mode"], "tight")
        self.assertEqual(effective_max_open("skim_swarm"), 5)
        boosts = session_entry_boosts("skim_swarm")
        self.assertGreater(boosts["enter_long_delta_boost"], 0)

    def test_over_churn_tightens(self):
        from utils.swarm_session_si import adapt_swarm_session, session_cycle_interval_mult

        # 24 exits, 8 wins / 16 losses => WR ~33%, negative pnl
        self._write_learned("skim_swarm", "MSFT", exits=24, wins=8, losses=16, pnl=-4.0)
        with patch("utils.swarm_session_si._session_date", return_value="2026-05-27"):
            pol = adapt_swarm_session("skim_swarm")
        self.assertTrue(pol["over_churn"])
        self.assertIn(pol["mode"], ("churn", "critical"))
        self.assertGreater(session_cycle_interval_mult("skim_swarm"), 1.0)

    def test_critical_when_both_anomalies(self):
        from utils.swarm_session_si import adapt_swarm_session

        self._write_learned("skim_swarm", "GOOG", exits=30, wins=9, losses=21, pnl=-8.0)
        with patch("utils.swarm_session_si._session_date", return_value="2026-05-27"):
            pol = adapt_swarm_session("skim_swarm", day_realized_pnl=-8.0)
        self.assertTrue(pol["negative_edge"])
        self.assertTrue(pol["over_churn"])
        self.assertEqual(pol["mode"], "critical")
        self.assertEqual(pol["max_open_effective"], 3)

    def test_integrity_scan_surfaces_churn(self):
        from utils.swarm_session_si import adapt_swarm_session
        from utils.integrity_diagnostics import scan_swarm_session_policy

        self._write_learned("skim_swarm", "PLTR", exits=25, wins=7, losses=18, pnl=-5.0)
        with patch("utils.swarm_session_si._session_date", return_value="2026-05-27"):
            adapt_swarm_session("skim_swarm")
        findings = scan_swarm_session_policy(component="skim_swarm")
        codes = [f["code"] for f in findings]
        self.assertTrue(any(c.startswith("swarm_") for c in codes))

    def test_get_params_merges_session_boosts(self):
        from agents.skim_swarm.symbol_learning import get_params
        from utils.swarm_session_si import save_session_policy

        save_session_policy(
            "skim_swarm",
            {
                "mode": "tight",
                "enter_long_delta_boost": 0.03,
                "enter_short_delta_boost": -0.03,
                "pause_new_entries": False,
            },
        )
        learned = {"params": {}, "overlay": {}}
        with patch("agents.skim_swarm.symbol_learning.load_learned", return_value=learned):
            with patch("agents.skim_swarm.symbol_learning.ensure_intraday_state"):
                with patch("agents.skim_swarm.symbol_learning._review_param_overrides", return_value={}):
                    with patch("agents.skim_swarm.symbol_learning.merge_overlay_into_params", return_value={}):
                        with patch("agents.skim_swarm.symbol_learning.thin_etf_symbols", return_value=set()):
                            p = get_params("TEST")
        self.assertAlmostEqual(p["enter_long"], 0.25, places=4)
        self.assertAlmostEqual(p["enter_short"], -0.25, places=4)


if __name__ == "__main__":
    unittest.main()
