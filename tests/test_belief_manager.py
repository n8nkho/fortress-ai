import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@patch("utils.belief_manager._lesson_llm", return_value="test lesson")
class TestBeliefManager(unittest.TestCase):
    def setUp(self):
        self.td = Path(__file__).resolve().parent / "_bm_td"
        self.td.mkdir(exist_ok=True)
        (self.td / "data" / "beliefs").mkdir(parents=True, exist_ok=True)
        self.old_root = __import__("os").environ.get("FORTRESS_AI_PROJECT_ROOT")
        __import__("os").environ["FORTRESS_AI_PROJECT_ROOT"] = str(self.td)

    def tearDown(self):
        import os
        import shutil

        if self.old_root:
            os.environ["FORTRESS_AI_PROJECT_ROOT"] = self.old_root
        else:
            os.environ.pop("FORTRESS_AI_PROJECT_ROOT", None)
        shutil.rmtree(self.td, ignore_errors=True)

    def test_add_and_merge(self, _mock_lesson):
        from utils import belief_manager as bm

        bm.save_beliefs([])
        r1 = bm.add_or_update_belief(
            symbol="SPY",
            regime_at_entry="NEUTRAL_RANGING",
            strategy_used="mean_reversion",
            entry_signal_confidence=0.7,
            pnl=50.0,
            pnl_pct=1.2,
            hold_duration_hours=4.0,
        )
        self.assertEqual(r1["outcome"], "win")
        rows = bm.load_beliefs()
        self.assertEqual(len(rows), 1)
        r2 = bm.add_or_update_belief(
            symbol="QQQ",
            regime_at_entry="NEUTRAL_RANGING",
            strategy_used="mean_reversion",
            entry_signal_confidence=0.6,
            pnl=30.0,
            pnl_pct=0.5,
            hold_duration_hours=2.0,
        )
        self.assertEqual(r2["confirmation_count"], 2)

    def test_context_format(self, _mock_lesson):
        from utils import belief_manager as bm

        bm.save_beliefs([])
        bm.add_or_update_belief(
            symbol="X",
            regime_at_entry="NEUTRAL_RANGING",
            strategy_used="mean_reversion",
            entry_signal_confidence=0.5,
            pnl=-10,
            pnl_pct=-0.5,
            hold_duration_hours=1.0,
        )
        s = bm.format_beliefs_prompt_section("NEUTRAL_RANGING", "mean_reversion")
        self.assertIn("LEARNED BELIEFS", s)


if __name__ == "__main__":
    unittest.main()
