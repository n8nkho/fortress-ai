"""Per-symbol skim learning."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.symbol_learning import improve_from_history, load_learned, record_decision, save_learned


class TestSkimSymbolLearning(unittest.TestCase):
    def test_improve_after_exits(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                L = load_learned("AAPL")
                L["stats"]["exits"] = 3
                L["stats"]["wins"] = 1
                L["stats"]["losses"] = 2
                save_learned("AAPL", L)
                out = improve_from_history("AAPL")
                self.assertIsNotNone(out)
                L2 = load_learned("AAPL")
                self.assertGreaterEqual(int(L2["stats"]["improvement_cycles"]), 1)

    def test_record_decision_increments(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                record_decision(
                    "MSFT",
                    decision={"action": "wait", "reasoning": "x", "score": 0.1},
                    act_result={"executed": False},
                    features={"r5m": 0.001, "side": "flat"},
                )
                L = load_learned("MSFT")
                self.assertEqual(int(L["stats"]["decisions"]), 1)


if __name__ == "__main__":
    unittest.main()
