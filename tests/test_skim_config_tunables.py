"""Skim config denylist and tunable confidence floor."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestSkimConfigAndTunables(unittest.TestCase):
    def test_runtime_denylist_includes_review_pause_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            swarm = Path(td) / "skim_swarm"
            swarm.mkdir(parents=True)
            (swarm / "runtime_overrides.json").write_text(
                json.dumps({"review_actions": {"pause_symbols": ["LLY", "MA"]}}),
                encoding="utf-8",
            )
            with patch("utils.skim_swarm_config._swarm_data_dir_path", return_value=swarm):
                from utils.skim_swarm_config import runtime_denylist

                deny = runtime_denylist()
                self.assertIn("LLY", deny)
                self.assertIn("MA", deny)

    def test_confidence_override_cannot_loosen_env_floor(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["FORTRESS_AI_DATA_DIR"] = td
            os.environ["FORTRESS_AI_MIN_CONFIDENCE"] = "0.75"
            Path(td, "tunable_params_overrides.json").write_text(
                json.dumps({"confidence_threshold": 0.65}),
                encoding="utf-8",
            )
            from utils.tunable_overrides import get_confidence_threshold

            self.assertEqual(get_confidence_threshold(), 0.75)


if __name__ == "__main__":
    unittest.main()
