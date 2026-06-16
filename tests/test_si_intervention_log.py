"""SI intervention log scoring."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestSiInterventionLog(unittest.TestCase):
    def test_no_op_actions_excluded_from_success_rate(self):
        from utils import si_intervention_log as log

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            log_path = data / "si_capability" / "interventions.jsonl"
            log_path.parent.mkdir(parents=True)
            rows = [
                {
                    "component": "skim_swarm",
                    "action": "swarm_session_normal",
                    "metrics_snapshot": {
                        "skim_swarm": {"rolling_expectancy_usd": None, "session_expectancy_usd": 0.0}
                    },
                },
                {
                    "component": "skim_swarm",
                    "action": "edge_autofix",
                    "metrics_snapshot": {
                        "skim_swarm": {"rolling_expectancy_usd": None, "session_expectancy_usd": -0.05}
                    },
                },
            ]
            log_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

            orig = log.intervention_log_path
            log.intervention_log_path = lambda: log_path  # type: ignore[assignment]
            try:
                rate = log.intervention_success_rate(
                    {"skim_swarm": {"session_expectancy_usd": 0.02}}
                )
            finally:
                log.intervention_log_path = orig  # type: ignore[assignment]

            self.assertAlmostEqual(rate or 0.0, 1.0)

    def test_insufficient_actionable_returns_none(self):
        from utils import si_intervention_log as log

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            log_path = data / "si_capability" / "interventions.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text(
                json.dumps(
                    {
                        "component": "skim_swarm",
                        "action": "swarm_session_normal",
                        "metrics_snapshot": {"skim_swarm": {}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            orig = log.intervention_log_path
            log.intervention_log_path = lambda: log_path  # type: ignore[assignment]
            try:
                self.assertIsNone(log.intervention_success_rate({"skim_swarm": {}}))
            finally:
                log.intervention_log_path = orig  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
