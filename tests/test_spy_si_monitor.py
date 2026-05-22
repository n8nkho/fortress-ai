"""SPY SI monitor must not halt on threshold-only zero fills."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.spy_self_improvement_engine import SpySelfImprovementEngine


class TestSpySiMonitor(unittest.TestCase):
    def test_skips_halt_when_only_threshold_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            dec = data / "decisions.jsonl"
            lines = []
            for _ in range(12):
                lines.append(
                    json.dumps(
                        {
                            "decision": {"action": "add_long", "confidence": 0.72},
                            "act": {"executed": False, "detail": "confidence_below_threshold:0.72<0.85"},
                        }
                    )
                )
            dec.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with patch("utils.spy_agent_config.spy_data_dir", return_value=data):
                with patch("utils.spy_tunable_overrides.spy_data_dir", return_value=data):
                    with patch("agents.spy_self_improvement_engine.spy_data_dir", return_value=data):
                        eng = SpySelfImprovementEngine()
                        out = eng.monitor_performance()
            self.assertIsNotNone(out)
            self.assertEqual(out.get("reason"), "zero_executions_threshold_gating_only")


if __name__ == "__main__":
    unittest.main()
