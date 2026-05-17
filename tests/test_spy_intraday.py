"""SPY intraday agent unit tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestSpyIntraday(unittest.TestCase):
    def test_ladder_rung_sizing(self):
        from agents.spy_intraday.ladder import can_add_rung, shares_for_rung

        with patch("agents.spy_intraday.ladder.rung_notional_usd", return_value=3333.33):
            self.assertGreaterEqual(shares_for_rung(500.0), 6)
        state = {"side": "flat", "rungs_open": 0, "max_rungs": 3}
        self.assertTrue(can_add_rung(state, "long"))
        state["rungs_open"] = 3
        self.assertFalse(can_add_rung(state, "long"))

    def test_eod_phases(self):
        from agents.spy_intraday import eod

        with patch("agents.spy_intraday.eod.et_now") as m:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            m.return_value = datetime(2026, 5, 16, 15, 55, tzinfo=ZoneInfo("America/New_York"))
            self.assertEqual(eod.describe_eod_phase(), "force_flatten")

    def test_filter_eod_actions(self):
        from agents.spy_intraday.eod import filter_allowed_actions

        all_a = {"wait", "add_long", "trim", "flatten_all"}
        limited = filter_allowed_actions(all_a, eod_caution=True)
        self.assertIn("flatten_all", limited)
        self.assertNotIn("add_long", limited)

    def test_build_market_context_shape(self):
        from agents.spy_intraday.context import build_market_context

        with patch("agents.spy_intraday.context._long_term_spy_stats", return_value={"last_close": 500}):
            with patch("agents.spy_intraday.context._intraday_structure", return_value={"intraday_swell": "upward"}):
                with patch("agents.spy_intraday.context._macro_vix", return_value={"vix": 18}):
                    with patch(
                        "agents.spy_intraday.context._futures_overnight_context",
                        return_value={"enabled": True, "tone": "risk_on"},
                    ):
                        with patch(
                            "agents.spy_intraday.context._global_sessions_context",
                            return_value={"enabled": True, "overnight_summary": "global_mixed_overnight"},
                        ):
                            with patch("agents.spy_intraday.context._key_movers", return_value=[]):
                                with patch("agents.spy_intraday.context._regime_snapshot", return_value={}):
                                    with patch("agents.spy_intraday.context._qualitative_snippet", return_value={}):
                                        ctx = build_market_context()
        self.assertIn("long_term", ctx)
        self.assertIn("intraday", ctx)
        self.assertIn("futures", ctx)
        self.assertIn("global_sessions", ctx)
        self.assertEqual(ctx["intraday"]["intraday_swell"], "upward")

    def test_futures_context_structure(self):
        from agents.spy_intraday.context import _futures_overnight_context

        stub = {"symbol": "ES=F", "last": 5200.0, "day_chg_pct": 0.3}
        with patch("agents.spy_intraday.context._day_change_pct", return_value=stub):
            ctx = _futures_overnight_context()
        self.assertTrue(ctx.get("enabled"))
        self.assertEqual(len(ctx.get("contracts") or []), 4)
        self.assertIn(ctx.get("tone"), ("risk_on", "risk_off", "neutral"))

    def test_spy_data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"FORTRESS_SPY_DATA_DIR": td}):
                from importlib import reload
                import utils.spy_agent_config as cfg

                reload(cfg)
                p = cfg.spy_data_dir()
                self.assertEqual(p, Path(td))


if __name__ == "__main__":
    unittest.main()
