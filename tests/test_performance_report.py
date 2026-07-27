"""Performance report always surfaces SI effectiveness."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.performance_report import (
    build_si_effectiveness_slice,
    format_performance_report_markdown,
    recommend_si_fixes,
)


class TestPerformanceReportSI(unittest.TestCase):
    def test_recommend_si_fixes_includes_cap_and_rate(self) -> None:
        fixes = recommend_si_fixes(
            rate=None,
            gaps=[{"objective_id": "skim_payoff_ratio", "metric": "rolling_payoff_ratio"}],
            skim={"rolling_payoff_ratio": 0.27, "rolling_expectancy_usd": -0.04},
            infra={"rolling_expectancy_usd": -0.01},
            open_by_disposition={"pending_human_go": 2},
        )
        self.assertTrue(any("payoff" in f.lower() for f in fixes))
        self.assertTrue(any("intervention_success_rate" in f for f in fixes))

    def test_si_slice_has_marker(self) -> None:
        with patch(
            "utils.performance_report.recommend_si_fixes",
            return_value=["keep monitoring"],
        ):
            with patch("utils.si_capability_review.collect_metrics", return_value={}):
                with patch(
                    "utils.si_capability_review.evaluate_objective_gaps",
                    return_value=[],
                ):
                    with patch(
                        "utils.si_intervention_log.intervention_success_rate",
                        return_value=None,
                    ):
                        with patch(
                            "utils.si_recommendation_queue.load_queue",
                            return_value={"items": []},
                        ):
                            slice_ = build_si_effectiveness_slice({})
        self.assertEqual(slice_.get("marker"), "si_effectiveness_in_performance_report")
        self.assertIn("recommended_fixes", slice_)

    def test_markdown_includes_si_section(self) -> None:
        report = {
            "session_date_et": "2026-07-27",
            "pnl": {
                "skim": {"realized_usd": 0.01, "exit_count": 1},
                "infra": {"realized_usd": 2.1, "exit_count": 1},
                "combined_realized_usd": 2.11,
            },
            "portfolio": {
                "alpha_vs_spy_pct": -0.05,
                "session_exit_count": 2,
                "strong_tape_1d": False,
                "participation_shortfall_exits": 0,
            },
            "si_effectiveness": {
                "intervention_success_rate": None,
                "target_min": 0.35,
                "skim_rolling_expectancy_usd": -0.04,
                "skim_rolling_payoff_ratio": 0.27,
                "infra_rolling_expectancy_usd": 0.0,
                "open_queue_by_disposition": {},
                "recommended_fixes": ["test fix"],
                "deferred_auto_implement": [
                    {"code": "si_gap_action_registry", "execute_after_et": "2026-07-29", "disposition": "auto_implement_queued"}
                ],
            },
        }
        md = format_performance_report_markdown(report)
        self.assertIn("SI effectiveness", md)
        self.assertIn("Recommended fixes", md)
        self.assertIn("si_gap_action_registry", md)


if __name__ == "__main__":
    unittest.main()
