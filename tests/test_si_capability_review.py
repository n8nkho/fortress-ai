"""Continuous SI capability review — objectives, gaps, meta-knob proposals."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_capability_review import (
    apply_capability_updates,
    evaluate_objective_gaps,
    propose_capability_updates,
    run_capability_review_cycle,
)


class TestSiCapabilityReview(unittest.TestCase):
    def test_evaluate_objective_gaps_negative_expectancy(self):
        metrics = {
            "skim_swarm": {
                "rolling_expectancy_usd": -0.2,
                "rolling_exits": 10,
                "rolling_payoff_ratio": 0.8,
            },
            "infra_swarm": {"rolling_expectancy_usd": 0.1, "rolling_exits": 8},
        }
        objectives = [
            {
                "id": "skim_session_expectancy",
                "component": "skim_swarm",
                "metric": "rolling_expectancy_usd",
                "target_min": 0.0,
                "min_exits": 4,
                "priority": "critical",
            }
        ]
        with patch("utils.si_capability_review.load_objectives", return_value=objectives):
            gaps = evaluate_objective_gaps(metrics)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["objective_id"], "skim_session_expectancy")

    def test_apply_capability_updates_persists(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            cap = data / "si_capability"
            cap.mkdir(parents=True)
            with patch("utils.si_capability_review._capability_dir", return_value=cap):
                with patch("utils.si_capability_review.overrides_path", return_value=cap / "overrides.json"):
                    applied = apply_capability_updates(
                        [
                            {
                                "capability": "edge_autofix_min_exits",
                                "current": 4,
                                "proposed": 5,
                                "reason": "test",
                            }
                        ]
                    )
                    self.assertEqual(len(applied), 1)
                    doc = json.loads((cap / "overrides.json").read_text())
                    self.assertEqual(doc["capabilities"]["edge_autofix_min_exits"], 5)

    def test_propose_increases_review_cadence_when_gaps(self):
        metrics = {"skim_swarm": {"rolling_expectancy_usd": -0.1, "rolling_exits": 10}}
        gaps = [{"objective_id": "x", "priority": "high"}]
        with patch("utils.si_capability_review.get_capability", return_value=1.0):
            props = propose_capability_updates(metrics, gaps)
        cadence = [p for p in props if p.get("capability") == "rth_review_cadence_mult"]
        self.assertTrue(cadence)
        self.assertLess(cadence[0]["proposed"], 1.0)

    def test_run_cycle_dry_shape(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            (data / "skim_swarm").mkdir(parents=True)
            (data / "infra_swarm").mkdir(parents=True)
            (data / "skim_swarm" / "decisions.jsonl").write_text("", encoding="utf-8")
            (data / "infra_swarm" / "decisions.jsonl").write_text("", encoding="utf-8")
            cap = data / "si_capability"
            cap.mkdir()
            root = Path(__file__).resolve().parent.parent
            with patch("utils.si_capability_review._data_dir", return_value=data):
                with patch("utils.si_capability_review._capability_dir", return_value=cap):
                    with patch("utils.si_capability_review.objectives_path", return_value=root / "config" / "si_objectives.json"):
                        with patch("utils.si_capability_review.capability_registry_path", return_value=root / "config" / "si_capability_registry.json"):
                            with patch("utils.si_capability_review._upsert_capability_queue_findings"):
                                report = run_capability_review_cycle(apply=False)
            self.assertTrue(report.get("ok"))
            self.assertIn("metrics", report)
            self.assertIn("objective_gaps", report)


class TestClassicCapabilityObjectives(unittest.TestCase):
    def test_classic_metrics_from_sibling(self):
        from utils.classic_bridge import classic_rolling_metrics

        m = classic_rolling_metrics(window_sessions=5)
        self.assertEqual(m.get("component"), "classic_fortress")
        self.assertIn("avg_candidates_per_screen", m)
        self.assertIn("rolling_fills", m)

    def test_classic_fill_gap_detected(self):
        metrics = {
            "classic_fortress": {
                "rolling_fills": 0,
                "screens_sampled": 5,
                "avg_candidates_per_screen": 0,
            }
        }
        objectives = [
            {
                "id": "classic_fill_activity",
                "component": "classic_fortress",
                "metric": "rolling_fills",
                "target_min": 1.0,
                "min_exits": 0,
                "priority": "critical",
            }
        ]
        with patch("utils.si_capability_review.load_objectives", return_value=objectives):
            gaps = evaluate_objective_gaps(metrics)
        self.assertTrue(any(g["objective_id"] == "classic_fill_activity" for g in gaps))


if __name__ == "__main__":
    unittest.main()
