"""SPY intraday self-improvement engine tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestSpySelfImprovement(unittest.TestCase):
    def test_validate_and_apply_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            with patch("utils.spy_agent_config.spy_data_dir", return_value=data):
                with patch("utils.spy_tunable_overrides.spy_data_dir", return_value=data):
                    from agents.spy_self_improvement_engine import SpySelfImprovementEngine
                    from utils.spy_tunable_overrides import current_snapshot, load_overrides

                    eng = SpySelfImprovementEngine()
                    raw = {
                        "parameter": "spy_min_confidence",
                        "proposed_value": 0.83,
                        "reasoning": "test",
                    }
                    val = eng.validate_proposal(raw)
                    self.assertIsNotNone(val)
                    eng._apply(val, "test-id")
                    self.assertAlmostEqual(load_overrides()["spy_min_confidence"], 0.83)
                    snap = current_snapshot()
                    self.assertAlmostEqual(snap["spy_min_confidence"], 0.83)

    def test_heuristic_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            dec = data / "decisions.jsonl"
            rows = []
            for i in range(12):
                rows.append(
                    json.dumps(
                        {
                            "decision": {"action": "wait", "confidence": 0.9},
                            "act": {"executed": False},
                        }
                    )
                )
            dec.write_text("\n".join(rows) + "\n", encoding="utf-8")
            with patch("utils.spy_agent_config.spy_data_dir", return_value=data):
                with patch("utils.spy_tunable_overrides.spy_data_dir", return_value=data):
                    with patch("agents.spy_self_improvement_engine.spy_data_dir", return_value=data):
                        from agents.spy_self_improvement_engine import SpySelfImprovementEngine

                        eng = SpySelfImprovementEngine()
                        bundle = eng.propose_heuristic()
                        self.assertEqual(bundle["proposal"]["parameter"], "spy_min_confidence")

    def test_maybe_improve_skips_until_n(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            with patch("utils.spy_agent_config.spy_data_dir", return_value=data):
                with patch("utils.spy_tunable_overrides.spy_data_dir", return_value=data):
                    with patch("agents.spy_self_improvement_engine.spy_data_dir", return_value=data):
                        from agents.spy_self_improvement_engine import SpySelfImprovementEngine

                        eng = SpySelfImprovementEngine()
                        with patch.dict(
                            "os.environ",
                            {"FORTRESS_SPY_SI_ENABLED": "1", "FORTRESS_SPY_SI_EVERY_N_CYCLES": "5"},
                        ):
                            for _ in range(4):
                                self.assertIsNone(eng.maybe_improve_after_cycle())
                            with patch.object(eng, "analyze_and_propose", return_value={"ok": True}):
                                out = eng.maybe_improve_after_cycle()
                                self.assertEqual(out, {"ok": True})


if __name__ == "__main__":
    unittest.main()
