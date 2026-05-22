"""Per-symbol skim learning."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.symbol_learning import (
    improve_from_history,
    load_learned,
    record_decision,
    save_learned,
)


class TestSkimSymbolLearning(unittest.TestCase):
    def test_improve_tightens_losing_side(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    L = load_learned("AAPL")
                    L["session_stats"]["exits"] = 15
                    L["session_stats"]["wins"] = 4
                    L["session_stats"]["losses"] = 11
                    L["session_stats"]["sum_pnl_usd"] = -1.0
                    L["session_stats"]["short_exits"] = 15
                    L["session_stats"]["short_pnl_usd"] = -1.2
                    save_learned("AAPL", L)
                    out = improve_from_history("AAPL")
                    self.assertIsNotNone(out)
                    L2 = load_learned("AAPL")
                    self.assertGreater(float(L2["params"]["enter_short_delta"]), 0)

    def test_session_resets_stats_keeps_params(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    L = load_learned("AAPL")
                    L["params"]["enter_short_delta"] = 0.05
                    L["session_stats"]["exits"] = 5
                    save_learned("AAPL", L)
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-23"):
                    L2 = load_learned("AAPL")
                    self.assertEqual(int(L2["session_stats"]["exits"]), 0)
                    self.assertAlmostEqual(float(L2["params"]["enter_short_delta"]), 0.05)

    def test_pattern_stats_on_exit(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    record_decision(
                        "SOXX",
                        decision={"action": "enter_long", "reasoning": "pullback_uptrend score=0.30", "score": 0.3},
                        act_result={"executed": True},
                        features={"r5m": 0.001, "side": "flat", "spy_r5m": 0.0005},
                    )
                    record_decision(
                        "SOXX",
                        decision={"action": "exit_position", "reasoning": "skim_target_hit:0.2"},
                        act_result={"executed": True},
                        features={"unrealized_usd": 0.2, "side": "long"},
                    )
                    L = load_learned("SOXX")
                    ps = L["pattern_stats"]["pullback_uptrend"]
                    self.assertEqual(int(ps["exits"]), 1)
                    self.assertAlmostEqual(float(ps["sum_pnl_usd"]), 0.2)

    def test_record_decision_increments(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    record_decision(
                        "MSFT",
                        decision={"action": "wait", "reasoning": "x", "score": 0.1},
                        act_result={"executed": False},
                        features={"r5m": 0.001, "side": "flat"},
                    )
                    L = load_learned("MSFT")
                    self.assertEqual(int(L["session_stats"]["decisions"]), 1)


if __name__ == "__main__":
    unittest.main()
