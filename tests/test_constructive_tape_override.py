"""Constructive-tape override + Classic demotion SI wiring."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.portfolio_session.constructive_tape_override import (
    maybe_allow_despite_underperformance,
)
from utils.portfolio_session.gates.market_relative_underperformance_gate import (
    MarketRelativeUnderperformanceGate,
)
from utils.si_capability_review import evaluate_objective_gaps


class TestConstructiveTapeOverride(unittest.TestCase):
    def test_allows_on_strong_tape_soft_band(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_CONSTRUCTIVE_TAPE_OVERRIDE": "1",
                    "FORTRESS_MR_DEEP_ALPHA_FLOOR": "-1.0",
                    "FORTRESS_MR_SOFT_ALPHA_THRESHOLD": "-0.8",
                    "FORTRESS_MR_TAPE_OVERRIDE_MAX_PER_DAY": "6",
                },
                clear=False,
            ):
                allow, detail = maybe_allow_despite_underperformance(
                    -0.9,
                    hard_threshold=-0.5,
                    session_state={
                        "strong_tape_1d": True,
                        "participation_shortfall_exits": 2,
                        "session_exit_count": 1,
                        "session_expectancy_usd": 0.01,
                    },
                )
                self.assertTrue(allow)
                self.assertIn("constructive_tape_entry_override", detail)

    def test_rejects_deep_alpha(self) -> None:
        allow, reason = maybe_allow_despite_underperformance(
            -1.2,
            hard_threshold=-0.5,
            session_state={
                "strong_tape_1d": True,
                "participation_shortfall_exits": 3,
            },
        )
        self.assertFalse(allow)
        self.assertIn("deep_alpha_floor", reason)

    def test_rejects_without_strong_tape(self) -> None:
        allow, reason = maybe_allow_despite_underperformance(
            -0.9,
            hard_threshold=-0.5,
            session_state={
                "strong_tape_1d": False,
                "participation_shortfall_exits": 2,
            },
        )
        self.assertFalse(allow)
        self.assertEqual(reason, "not_strong_tape")

    def test_gate_allows_via_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_CONSTRUCTIVE_TAPE_OVERRIDE": "1",
                    "FORTRESS_MR_DEEP_ALPHA_FLOOR": "-1.0",
                    "FORTRESS_MR_SOFT_ALPHA_THRESHOLD": "-0.8",
                },
                clear=False,
            ):
                gate = MarketRelativeUnderperformanceGate(threshold=-0.5)
                result = gate.evaluate(
                    {
                        "alpha_vs_spy_pct": -0.9,
                        "benchmark_ok": True,
                        "strong_tape_1d": True,
                        "participation_shortfall_exits": 2,
                        "session_exit_count": 0,
                    }
                )
                self.assertFalse(result.blocked)
                self.assertEqual(result.reason, "constructive_tape_entry_override")

    def test_gate_still_blocks_without_override_context(self) -> None:
        gate = MarketRelativeUnderperformanceGate(threshold=-0.5)
        result = gate.evaluate(
            {
                "alpha_vs_spy_pct": -0.9,
                "benchmark_ok": True,
                "strong_tape_1d": False,
                "participation_shortfall_exits": 0,
            }
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.reason, "market_relative_underperformance")

    def test_session_underperforming_allows_override_on_shortfall(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_CONSTRUCTIVE_TAPE_OVERRIDE": "1",
                    "FORTRESS_MR_DEEP_ALPHA_FLOOR": "-1.0",
                    "FORTRESS_MR_SOFT_ALPHA_THRESHOLD": "-0.8",
                },
                clear=False,
            ):
                gate = MarketRelativeUnderperformanceGate(threshold=-0.5)
                result = gate.evaluate(
                    {
                        "session_underperforming": True,
                        "alpha_vs_spy_pct": -0.9,
                        "benchmark_ok": True,
                        "strong_tape_1d": True,
                        "participation_shortfall_exits": 1,
                        "session_exit_count": 5,
                    }
                )
                self.assertFalse(result.blocked)
                self.assertEqual(result.reason, "constructive_tape_entry_override")

    def test_pre_trade_gate_allows_override_on_participation_shortfall(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_CONSTRUCTIVE_TAPE_OVERRIDE": "1",
                    "FORTRESS_MR_DEEP_ALPHA_FLOOR": "-1.0",
                    "FORTRESS_MR_SOFT_ALPHA_THRESHOLD": "-0.8",
                },
                clear=False,
            ):
                from utils.portfolio_session.pre_trade_gate import check_market_relative_underperformance

                result = check_market_relative_underperformance(
                    {
                        "alpha_vs_spy_pct": -0.9,
                        "benchmark_ok": True,
                        "strong_tape_1d": True,
                        "participation_shortfall_exits": 1,
                        "session_exit_count": 5,
                    }
                )
                self.assertFalse(result.blocked)
                self.assertEqual(result.reason, "constructive_tape_entry_override")


class TestClassicSleeveDemotion(unittest.TestCase):
    def test_skips_classic_gaps_when_fills_stale(self) -> None:
        metrics = {
            "classic_fortress": {
                "rolling_fills": 0,
                "days_since_last_fill": 20.0,
                "avg_candidates_per_screen": 0.0,
                "screens_sampled": 5,
                "rolling_expectancy_usd": None,
            }
        }
        gaps = evaluate_objective_gaps(metrics)
        classic_ids = {g.get("objective_id") for g in gaps if g.get("component") == "classic_fortress"}
        self.assertNotIn("classic_fill_activity", classic_ids)
        self.assertNotIn("classic_candidate_throughput", classic_ids)
        self.assertNotIn("classic_fill_recency", classic_ids)

    def test_emits_classic_gap_when_recent_fills(self) -> None:
        metrics = {
            "classic_fortress": {
                "rolling_fills": 0,
                "days_since_last_fill": 8.0,
                "avg_candidates_per_screen": 0.0,
                "screens_sampled": 5,
            }
        }
        gaps = evaluate_objective_gaps(metrics)
        classic_ids = {g.get("objective_id") for g in gaps if g.get("component") == "classic_fortress"}
        # Not demoted (<10 idle days): fill activity + recency gaps still fire.
        self.assertIn("classic_fill_activity", classic_ids)
        self.assertIn("classic_fill_recency", classic_ids)


if __name__ == "__main__":
    unittest.main()
