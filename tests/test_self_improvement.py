"""Safety and bounds tests for Tier-1 self-improvement (no live LLM)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _fresh_engine(td: Path):
    """Import engine module with FORTRESS_AI_DATA_DIR pointing at temp dir."""
    os.environ["FORTRESS_AI_DATA_DIR"] = str(td)
    import importlib

    import agents.self_improvement_engine as sie

    importlib.reload(sie)
    return sie.get_engine(), sie


class TestSelfImprovementSafety(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._td, ignore_errors=True)
        self._prev_data = os.environ.get("FORTRESS_AI_DATA_DIR")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._prev_data is None:
            os.environ.pop("FORTRESS_AI_DATA_DIR", None)
        else:
            os.environ["FORTRESS_AI_DATA_DIR"] = self._prev_data

    def test_validate_rejects_unknown_parameter(self):
        eng, _ = _fresh_engine(Path(self._td))
        self.assertIsNone(
            eng.validate_proposal_json(
                {
                    "parameter": "max_position_size_pct",
                    "proposed_value": 0.01,
                    "reasoning": "x",
                }
            )
        )

    def test_validate_rejects_out_of_bounds(self):
        eng, _ = _fresh_engine(Path(self._td))
        self.assertIsNone(
            eng.validate_proposal_json(
                {
                    "parameter": "confidence_threshold",
                    "proposed_value": 0.2,
                    "reasoning": "x",
                }
            )
        )

    def test_immutable_keys_not_tunable(self):
        _, sie = _fresh_engine(Path(self._td))
        for k in sie.IMMUTABLE_CONSTRAINTS:
            self.assertNotIn(k, sie.TUNABLE_BOUNDS)

    def test_velocity_blocks_after_weekly_cap(self):
        td = Path(self._td)
        eng, sie = _fresh_engine(td)
        log = td / "self_improvement_log.jsonl"
        now = "2026-05-02T12:00:00+00:00"
        lines = [json.dumps({"timestamp": now, "decision": "approved_human"}) for _ in range(sie.MAX_CHANGES_PER_WEEK)]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, reason = eng._velocity_ok()
        self.assertFalse(ok)
        self.assertIn("weekly", reason)

    def test_revert_clears_overrides_file(self):
        td = Path(self._td)
        eng, _ = _fresh_engine(td)
        ov = td / "tunable_params_overrides.json"
        td.mkdir(parents=True, exist_ok=True)
        ov.write_text(json.dumps({"confidence_threshold": 0.7}), encoding="utf-8")
        eng.revert_last_overrides(reason="test")
        self.assertFalse(ov.exists())

    def test_monitor_no_op_when_win_rate_unknown(self):
        eng, _ = _fresh_engine(Path(self._td))
        self.assertIsNone(eng.monitor_and_revert_if_needed())


if __name__ == "__main__":
    unittest.main()
