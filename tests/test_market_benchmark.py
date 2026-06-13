"""Market benchmark vs portfolio session — SI learning."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.market_benchmark import (
    build_portfolio_session_metrics,
    market_relative_findings,
)


class TestMarketBenchmark(unittest.TestCase):
    def test_alpha_gap_on_strong_tape_underperformance(self):
        bench = {
            "ok": True,
            "benchmark": "SPY",
            "change_1d_pct": 0.54,
            "change_5d_pct": 1.2,
            "tape_trend": "uptrend",
            "strong_tape_1d": True,
        }
        port = {
            "session_realized_usd": -0.37,
            "session_return_pct": -0.0004,
            "session_exit_count": 4,
            "alpha_vs_spy_pct": -0.5404,
            "participation_shortfall_exits": 2,
            "benchmark_ok": True,
        }
        findings = market_relative_findings(port, benchmark=bench)
        codes = {f["code"] for f in findings}
        self.assertIn("market_relative_underperformance", codes)
        self.assertIn("market_participation_gap", codes)

    def test_no_finding_when_alpha_positive(self):
        bench = {"ok": True, "benchmark": "SPY", "change_1d_pct": 0.2, "strong_tape_1d": False}
        port = {
            "session_realized_usd": 1.0,
            "session_return_pct": 0.001,
            "session_exit_count": 8,
            "alpha_vs_spy_pct": 0.5,
            "participation_shortfall_exits": 0,
            "benchmark_ok": True,
        }
        self.assertEqual(market_relative_findings(port, benchmark=bench), [])

    def test_objective_gap_detected_from_metrics(self):
        from utils.si_capability_review import evaluate_objective_gaps

        metrics = {
            "portfolio_session": {
                "benchmark_ok": True,
                "alpha_vs_spy_pct": -0.55,
                "participation_shortfall_exits": 3,
                "rolling_exits": 3,
            }
        }
        objectives = [
            {
                "id": "portfolio_session_alpha_vs_spy",
                "component": "portfolio_session",
                "metric": "alpha_vs_spy_pct",
                "target_min": -0.25,
                "priority": "high",
            },
            {
                "id": "portfolio_participation_on_strong_tape",
                "component": "portfolio_session",
                "metric": "participation_shortfall_exits",
                "target_max": 0.0,
                "priority": "high",
            },
        ]
        with patch("utils.si_capability_review.load_objectives", return_value=objectives):
            gaps = evaluate_objective_gaps(metrics)
        ids = {g["objective_id"] for g in gaps}
        self.assertIn("portfolio_session_alpha_vs_spy", ids)
        self.assertIn("portfolio_participation_on_strong_tape", ids)

    @patch("utils.market_benchmark._session_combined_realized_usd", return_value=(-0.37, 4))
    def test_build_portfolio_session_metrics_shape(self, _mock_pnl):
        bench = {
            "ok": True,
            "benchmark": "SPY",
            "change_1d_pct": 0.54,
            "strong_tape_1d": True,
            "tape_trend": "uptrend",
        }
        port = build_portfolio_session_metrics(benchmark=bench, reference_equity_usd=100_000.0)
        self.assertEqual(port["session_realized_usd"], -0.37)
        self.assertIsNotNone(port["alpha_vs_spy_pct"])
        self.assertLess(float(port["alpha_vs_spy_pct"]), -0.25)


if __name__ == "__main__":
    unittest.main()
