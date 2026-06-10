"""Adaptive SI — rolling edge, symbol brakes, unified loser blocks."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_adaptive_actions import (
    adaptive_strength_from_gaps,
    apply_rolling_aware_edge_autofix,
    apply_symbol_session_brakes,
    apply_unified_loser_management,
    save_unified_si_state,
    run_adaptive_si_cycle,
    unified_symbol_blocked,
)


class TestAdaptiveStrength(unittest.TestCase):
    def test_strength_from_gaps(self):
        gaps = [
            {
                "component": "skim_swarm",
                "gap": 0.3,
                "priority": "critical",
            }
        ]
        with patch("utils.si_adaptive_actions._get_cap", return_value=0.55):
            s = adaptive_strength_from_gaps(gaps, component="skim_swarm")
        self.assertGreater(s, 0.1)
        self.assertLessEqual(s, 1.0)


class TestRollingEdgeAutofix(unittest.TestCase):
    def test_applies_when_session_green_rolling_bad(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            swarm = data / "skim_swarm"
            swarm.mkdir(parents=True)
            (swarm / "session_policy.json").write_text(
                json.dumps({"mode": "normal", "enter_long_delta_boost": 0.0}),
                encoding="utf-8",
            )
            (swarm / "runtime_overrides.json").write_text("{}", encoding="utf-8")

            gaps = [
                {
                    "component": "skim_swarm",
                    "gap": 0.25,
                    "priority": "high",
                    "objective_id": "skim_session_expectancy",
                }
            ]
            scorecard = {"payoff_ratio": 1.05, "exits": 10, "ok": True}

            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": str(data)}):
                with patch("utils.si_adaptive_actions._enabled", return_value=True):
                    with patch(
                        "utils.si_adaptive_actions.rolling_metrics",
                        return_value={
                            "rolling_payoff_ratio": 0.7,
                            "rolling_expectancy_usd": -0.15,
                            "rolling_exits": 8,
                        },
                    ):
                        with patch("utils.si_adaptive_actions._get_cap", return_value=0.55):
                            with patch(
                                "utils.si_capability_review.effective_edge_autofix_rr_boost_cap",
                                return_value=0.2,
                            ):
                                result = apply_rolling_aware_edge_autofix(
                                    "skim_swarm", scorecard, gaps=gaps
                                )

            self.assertEqual(result.get("mode"), "rolling_aware")
            pol = json.loads((swarm / "session_policy.json").read_text())
            self.assertEqual(pol.get("mode"), "rolling_aware")
            self.assertGreater(float(pol.get("enter_long_delta_boost") or 0), 0)


class TestSymbolBrakes(unittest.TestCase):
    def test_brake_on_session_loser(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            learned = data / "skim_swarm" / "learned"
            learned.mkdir(parents=True)
            (learned / "qqq.json").write_text(
                json.dumps(
                    {
                        "session_date_et": "2026-06-09",
                        "session_stats": {"exits": 4, "sum_pnl_usd": -1.2},
                        "params": {"enter_long_delta": 0.0},
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": str(data)}):
                with patch("utils.si_adaptive_actions._enabled", return_value=True):
                    with patch("utils.si_adaptive_actions._session_date", return_value="2026-06-09"):
                        with patch("utils.si_adaptive_actions._get_cap", return_value=1.0):
                            result = apply_symbol_session_brakes("skim_swarm")

            self.assertTrue(result.get("brakes"))
            doc = json.loads((learned / "qqq.json").read_text())
            self.assertLess(float(doc["params"]["enter_long_delta"]), 0.0)


class TestUnifiedLoser(unittest.TestCase):
    def test_blocks_symbol_after_trim_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            pf = {
                "connected": True,
                "equity": 100000.0,
                "positions": [{"symbol": "QQQ", "qty": 100, "unrealized_pl": -6000.0}],
            }
            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": str(data)}):
                with patch("utils.si_adaptive_actions._enabled", return_value=True):
                    with patch("utils.si_adaptive_actions._fortress_portfolio_snapshot", return_value=pf):
                        with patch("utils.si_adaptive_actions._get_cap", side_effect=lambda n, d: d):
                            with patch("agents.unified_ai_agent._dry_run", return_value=True):
                                with patch("agents.unified_ai_agent.observe", return_value={}):
                                    result = apply_unified_loser_management()
                                    self.assertTrue(result.get("actions"))
                                    blocked, reason = unified_symbol_blocked("QQQ")
                                    self.assertTrue(blocked)
                                    self.assertIn("si_adaptive", reason or "")

    def test_unified_symbol_blocked_persists(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": str(data)}):
                save_unified_si_state(
                    {"symbol_actions": {"MDT": {"block_entries": True, "reason": "test"}}}
                )
                blocked, _ = unified_symbol_blocked("MDT")
            self.assertTrue(blocked)


class TestAdaptiveCycle(unittest.TestCase):
    def test_run_cycle_shape(self):
        with patch("utils.si_adaptive_actions._enabled", return_value=True):
            with patch(
                "utils.si_adaptive_actions.apply_rolling_aware_edge_autofix",
                return_value={"skipped": "test"},
            ):
                with patch(
                    "utils.si_adaptive_actions.apply_symbol_session_brakes",
                    return_value={"brakes": []},
                ):
                    with patch(
                        "utils.si_adaptive_actions.apply_unified_loser_management",
                        return_value={"skipped": "test"},
                    ):
                        out = run_adaptive_si_cycle(gaps=[], edge_context={})
        self.assertTrue(out.get("ok"))
        self.assertIn("skim_swarm", out)
        self.assertIn("unified_losers", out)


class TestTargetMaxGap(unittest.TestCase):
    def test_classic_recency_gap(self):
        from utils.si_capability_review import evaluate_objective_gaps

        metrics = {"classic_fortress": {"days_since_last_fill": 14, "rolling_fills": 0}}
        objectives = [
            {
                "id": "classic_fill_recency",
                "component": "classic_fortress",
                "metric": "days_since_last_fill",
                "target_max": 7.0,
                "priority": "high",
            }
        ]
        with patch("utils.si_capability_review.load_objectives", return_value=objectives):
            gaps = evaluate_objective_gaps(metrics)
        self.assertTrue(any(g["objective_id"] == "classic_fill_recency" for g in gaps))


if __name__ == "__main__":
    unittest.main()
