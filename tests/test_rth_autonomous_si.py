"""RTH autonomous SI and edge autofix."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.edge_autofix import apply_edge_autofix, session_rr_margin_boost
from utils.rth_autonomous_si import rth_intraday_si_enabled, run_rth_intraday_cycle, si_mutations_frozen


class TestEdgeAutofix(unittest.TestCase):
    def test_apply_tightens_inverted_payoff(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            skim = data / "skim_swarm"
            skim.mkdir(parents=True)
            (skim / "session_policy.json").write_text(json.dumps({"mode": "normal"}), encoding="utf-8")
            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": str(data)}):
                res = apply_edge_autofix(
                    "skim_swarm",
                    {
                        "ok": True,
                        "payoff_ratio": 0.6,
                        "profit_factor": 0.55,
                        "expectancy_usd": -0.05,
                        "exits": 10,
                        "by_pattern": {},
                    },
                )
                self.assertTrue(res.get("changes"))
                boost = session_rr_margin_boost("skim_swarm")
                self.assertGreater(boost, 0)


class TestRthCycle(unittest.TestCase):
    def test_skipped_outside_rth_when_not_forced(self):
        with patch.dict(os.environ, {"FORTRESS_RTH_INTRADAY_SI": "1"}):
            with patch("utils.us_equity_hours.is_us_equity_rth_et", return_value=False):
                r = run_rth_intraday_cycle(force=False)
                self.assertEqual(r.get("skipped"), "outside_rth")

    def test_enabled_flag(self):
        with patch.dict(os.environ, {"FORTRESS_RTH_INTRADAY_SI": "0"}):
            self.assertFalse(rth_intraday_si_enabled())

    def test_halt_freezes_mutations(self):
        with patch.dict(os.environ, {"FORTRESS_RTH_INTRADAY_SI": "1"}):
            with patch("utils.us_equity_hours.is_us_equity_rth_et", return_value=True):
                with patch("utils.operator_halt.is_trading_halted", return_value=True):
                    with patch("utils.integrity_diagnostics.run_integrity_scan") as scan_mock:
                        scan_mock.return_value = {"counts": {}, "findings": []}
                        with patch("utils.si_recommendation_queue.process_scan_to_queue") as queue_mock:
                            r = run_rth_intraday_cycle(force=True)
        self.assertTrue(r.get("frozen"))
        self.assertEqual(r.get("skipped"), "SI-FROZEN: trading_halted")
        queue_mock.assert_not_called()

    def test_si_mutations_frozen_helper(self):
        with patch("utils.operator_halt.is_trading_halted", return_value=True):
            self.assertIsNotNone(si_mutations_frozen())


if __name__ == "__main__":
    unittest.main()
