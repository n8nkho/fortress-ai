"""Per-symbol continuous intraday self-improvement."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.intraday_si import (
    adapt_from_block_streaks,
    adapt_last_exit_micro,
    merge_overlay_into_params,
    reset_intraday_session_state,
    session_expectancy,
)
from agents.skim_swarm.symbol_learning import get_params, load_learned, record_decision, save_learned


class TestSkimIntradaySi(unittest.TestCase):
    def test_merge_overlay_applies_per_symbol(self):
        learned = {"session_overlay": {"target_mult_overlay": 1.1, "stop_mult_overlay": 0.95}}
        params = {"target_mult": 1.0}
        with patch("agents.skim_swarm.intraday_si.stop_target_mult", return_value=0.70):
            out = merge_overlay_into_params(params, learned)
        self.assertAlmostEqual(out["target_mult_effective"], 1.1)
        self.assertAlmostEqual(out["stop_target_mult_effective"], 0.665)

    def test_block_streak_loosens_no_entry(self):
        learned = {"session_overlay": {}, "block_streaks": {"no_entry": 3}}
        params = {"enter_long_delta": 0.0, "enter_short_delta": 0.0}
        notes: list[str] = []
        with patch("agents.skim_swarm.intraday_si.continuous_si_enabled", return_value=True):
            with patch("agents.skim_swarm.intraday_si.block_streak_threshold", return_value=3):
                adapt_from_block_streaks(learned, params, notes)
        ov = learned["session_overlay"]
        self.assertLess(float(ov["enter_long_delta_boost"]), 0.0)
        self.assertGreater(float(ov["enter_short_delta_boost"]), 0.0)
        self.assertTrue(notes)

    def test_exit_micro_tightens_on_stop_loss(self):
        learned = {"session_overlay": {}, "recent_exit_streak": {}, "params": {"target_mult": 1.0, "cooldown_mult": 1.0}}
        params = learned["params"]
        notes: list[str] = []
        with patch("agents.skim_swarm.intraday_si.continuous_si_enabled", return_value=True):
            adapt_last_exit_micro(
                learned,
                params,
                exit_reasoning="stop_loss:-0.12",
                pnl_usd=-0.12,
                pattern="rip_fade",
                notes=notes,
            )
        self.assertLess(float(params["target_mult"]), 1.0)
        self.assertLess(float(learned["session_overlay"]["stop_mult_overlay"]), 1.0)
        self.assertTrue(notes)

    def test_session_reset_clears_overlay_keeps_params(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    L = load_learned("NVDA")
                    L["params"]["target_mult"] = 0.92
                    L["session_overlay"]["enter_long_delta_boost"] = 0.03
                    L["block_streaks"] = {"no_entry": 5}
                    save_learned("NVDA", L)
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-23"):
                    L2 = load_learned("NVDA")
                    self.assertAlmostEqual(float(L2["params"]["target_mult"]), 0.92)
                    self.assertEqual(float(L2["session_overlay"]["enter_long_delta_boost"]), 0.0)
                    self.assertEqual(L2["block_streaks"], {})

    def test_get_params_includes_overlay(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    L = load_learned("AAPL")
                    L["session_overlay"]["enter_long_delta_boost"] = 0.02
                    save_learned("AAPL", L)
                    p = get_params("AAPL")
                    self.assertGreater(float(p["enter_long"]), 0.22)
                    self.assertIn("stop_target_mult_effective", p)

    def test_record_exit_triggers_adaptation_log(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    with patch("agents.skim_swarm.symbol_learning.continuous_si_enabled", return_value=True):
                        with patch("agents.skim_swarm.symbol_learning.improve_every_exit", return_value=True):
                            record_decision(
                                "MSFT",
                                decision={"action": "enter_long", "reasoning": "pullback_uptrend score=0.30", "score": 0.3},
                                act_result={"executed": True},
                                features={"r5m": 0.001, "side": "flat", "spy_r5m": 0.0005},
                            )
                            record_decision(
                                "MSFT",
                                decision={"action": "exit_position", "reasoning": "stop_loss:-0.10"},
                                act_result={"executed": True},
                                features={"unrealized_usd": -0.10, "side": "long"},
                            )
                            L = load_learned("MSFT")
                            self.assertTrue(L.get("adaptation_log"))
                            self.assertEqual(int(L["session_stats"]["exits"]), 1)

    def test_session_expectancy(self):
        self.assertAlmostEqual(session_expectancy({"exits": 4, "sum_pnl_usd": -0.20}), -0.05)
        self.assertIsNone(session_expectancy({"exits": 0}))


if __name__ == "__main__":
    unittest.main()
