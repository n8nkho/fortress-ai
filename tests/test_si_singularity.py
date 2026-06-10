"""SI Singularity — surpass floor objectives."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.si_singularity import (
    combined_portfolio_realized,
    compute_phase,
    evaluate_surpass_gaps,
    run_singularity_cycle,
)


class TestSiSingularity(unittest.TestCase):
    def test_phase_deficit_when_floor_gaps(self):
        self.assertEqual(compute_phase([{"objective_id": "x"}], []), "deficit")

    def test_phase_surpass_when_aspire_gaps(self):
        self.assertEqual(compute_phase([], [{"objective_id": "y"}]), "surpass")

    def test_phase_singularity_when_no_gaps(self):
        self.assertEqual(compute_phase([], []), "singularity")

    def test_combined_portfolio(self):
        m = {
            "skim_swarm": {"rolling_realized_usd": 1.0},
            "infra_swarm": {"rolling_realized_usd": 2.5},
            "classic_fortress": {"rolling_realized_usd": 0.5},
            "unified_ai": {"rolling_realized_usd": 0.0},
        }
        self.assertEqual(combined_portfolio_realized(m), 4.0)

    def test_surpass_gaps_when_floor_met_below_aspire(self):
        metrics = {
            "skim_swarm": {
                "rolling_expectancy_usd": 0.03,
                "rolling_exits": 10,
                "rolling_payoff_ratio": 1.15,
            },
            "infra_swarm": {"rolling_expectancy_usd": 0.2, "rolling_exits": 8},
            "unified_ai": {"rolling_realized_usd": 0, "rolling_exits": 0},
            "classic_fortress": {
                "avg_candidates_per_screen": 0.5,
                "screens_sampled": 5,
                "rolling_fills": 1,
                "days_since_last_fill": 5,
            },
            "si_meta": {},
        }
        with patch("utils.si_singularity.load_state", return_value={"aspire_overrides": {}}):
            surpass = evaluate_surpass_gaps(metrics, [])
        ids = {g["objective_id"] for g in surpass}
        self.assertIn("skim_session_expectancy", ids)

    def test_run_cycle_disabled(self):
        with patch("utils.si_singularity.singularity_enabled", return_value=False):
            out = run_singularity_cycle({}, [])
        self.assertEqual(out.get("skipped"), "singularity_disabled")


if __name__ == "__main__":
    unittest.main()
