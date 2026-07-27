"""SI true-loop fixes: autofix exhausted, intervention scoring, symbol brakes, allowlist."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.edge_autofix import apply_edge_autofix, overlays_at_cap
from utils.si_code_implementation import _diff_allowed, can_auto_implement
from utils.si_intervention_log import intervention_success_rate, record_intervention


class TestEdgeAutofixExhausted(unittest.TestCase):
    def test_overlays_at_cap_true(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ov_path = root / "skim_swarm" / "runtime_overrides.json"
            ov_path.parent.mkdir(parents=True)
            ov_path.write_text(
                json.dumps(
                    {
                        "rr_safety_margin_session_boost": 0.25,
                        "target_mult_overlay_boost": 0.12,
                        "stop_mult_overlay_boost": 0.08,
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch(
                    "utils.si_capability_review.effective_edge_autofix_rr_boost_cap",
                    return_value=0.25,
                ):
                    self.assertTrue(overlays_at_cap("skim_swarm", rr_cap=0.25))

    def test_apply_skips_when_at_cap(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            swarm = root / "skim_swarm"
            swarm.mkdir(parents=True)
            (swarm / "runtime_overrides.json").write_text(
                json.dumps(
                    {
                        "rr_safety_margin_session_boost": 0.25,
                        "target_mult_overlay_boost": 0.12,
                        "stop_mult_overlay_boost": 0.08,
                    }
                ),
                encoding="utf-8",
            )
            (swarm / "session_policy.json").write_text("{}", encoding="utf-8")
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch(
                    "utils.si_capability_review.effective_edge_autofix_rr_boost_cap",
                    return_value=0.25,
                ):
                    with patch(
                        "utils.si_capability_review.effective_edge_autofix_min_exits",
                        return_value=4,
                    ):
                        out = apply_edge_autofix(
                            "skim_swarm",
                            {
                                "ok": True,
                                "payoff_ratio": 0.5,
                                "expectancy_usd": -0.1,
                                "exits": 9,
                                "profit_factor": 0.5,
                            },
                        )
            self.assertEqual(out.get("skipped"), "edge_autofix_exhausted")
            self.assertEqual(out.get("marker"), "edge_autofix_exhausted")


class TestInterventionScoring(unittest.TestCase):
    def test_exhausted_and_dedupe_not_scored_as_failures(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                metrics = {
                    "skim_swarm": {
                        "rolling_expectancy_usd": 0.03,
                        "rolling_payoff_ratio": 0.7,
                    }
                }
                for _ in range(5):
                    record_intervention(
                        component="skim_swarm",
                        action="edge_autofix_exhausted",
                        metrics_snapshot=metrics,
                        detail={"marker": "edge_autofix_exhausted"},
                        scoreable=False,
                    )
                record_intervention(
                    component="skim_swarm",
                    action="edge_autofix",
                    metrics_snapshot={
                        "skim_swarm": {
                            "rolling_expectancy_usd": 0.01,
                            "rolling_payoff_ratio": 0.5,
                        }
                    },
                    scoreable=True,
                )
                rate = intervention_success_rate(metrics)
                self.assertIsNotNone(rate)
                self.assertGreaterEqual(float(rate), 0.99)

    def test_legacy_pre_format_rows_do_not_poison_rate(self) -> None:
        """Jul-24-style spam without session_date_et must not force rate to 0."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "si_capability" / "interventions.jsonl"
            p.parent.mkdir(parents=True)
            legacy = {
                "ts": "2026-07-24T16:00:00-04:00",
                "component": "skim_swarm",
                "action": "edge_autofix",
                "metrics_snapshot": {
                    "skim_swarm": {
                        "rolling_expectancy_usd": 0.03,
                        "rolling_payoff_ratio": 0.7,
                    }
                },
            }
            with p.open("w", encoding="utf-8") as fh:
                for _ in range(8):
                    fh.write(json.dumps(legacy) + "\n")
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                rate = intervention_success_rate(
                    {
                        "skim_swarm": {
                            "rolling_expectancy_usd": -0.04,
                            "rolling_payoff_ratio": 0.27,
                        }
                    }
                )
            self.assertIsNone(rate)


class TestSymbolSessionBrake(unittest.TestCase):
    def test_single_large_loss_pauses_entries(self) -> None:
        from utils.si_adaptive_actions import apply_symbol_session_brakes

        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "skim_swarm" / "learned"
            learned.mkdir(parents=True)
            doc = {
                "session_date_et": "2099-01-02",
                "session_stats": {"exits": 1, "sum_pnl_usd": -0.78},
                "params": {},
            }
            (learned / "goog.json").write_text(json.dumps(doc), encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_SI_ADAPTIVE_ACTIONS": "1",
                    "FORTRESS_SI_SYMBOL_BRAKE_SINGLE_LOSS_USD": "0.25",
                },
                clear=False,
            ):
                with patch(
                    "utils.si_adaptive_actions._session_date",
                    return_value="2099-01-02",
                ):
                    out = apply_symbol_session_brakes("skim_swarm")
            self.assertTrue(out.get("brakes"))
            self.assertTrue(any("pause_entries" in b for b in out["brakes"]))
            saved = json.loads((learned / "goog.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["params"].get("pause_entries"))


class TestAllowlistAndDeferred(unittest.TestCase):
    def test_data_paths_ignored_in_allowlist(self) -> None:
        ok, reason = _diff_allowed(
            ["data/si_recommendation_queue.json", "utils/edge_autofix.py"]
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_unified_ai_paths_allowed(self) -> None:
        ok, reason = _diff_allowed(["unified_ai/__init__.py", "utils/edge_autofix.py"])
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_execute_after_blocks_until_date(self) -> None:
        item = {
            "status": "open",
            "disposition": "auto_implement_queued",
            "kind": "code_guard",
            "code": "si_gap_action_registry",
            "execute_after_et": "2099-12-31",
            "agent_assessment": {"worth_implementing": True},
        }
        with patch("utils.si_code_implementation.auto_code_enabled", return_value=True):
            with patch(
                "utils.si_recommendation_queue.is_cross_stack_item",
                return_value=False,
            ):
                ok, reason = can_auto_implement(item)
        self.assertFalse(ok)
        self.assertIn("execute_after_not_reached", reason)


if __name__ == "__main__":
    unittest.main()
