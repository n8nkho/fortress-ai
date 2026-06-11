"""Phase 3.2 — SI objectives must be expectancy-first (no win-rate primary targets)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_PRIMARY_FORBIDDEN = ("win_rate", "rolling_win_rate", "session_win_rate")
_PRIMARY_EXPECTANCY = (
    "rolling_expectancy_usd",
    "rolling_payoff_ratio",
    "rolling_realized_usd",
    "combined_rolling_realized_usd",
    "intervention_success_rate",
    "rolling_fills",
    "avg_candidates_per_screen",
    "days_since_last_fill",
)


class TestSiObjectivesExpectancyFirst(unittest.TestCase):
    def test_si_objectives_no_win_rate_primary(self):
        doc = json.loads((_ROOT / "config" / "si_objectives.json").read_text(encoding="utf-8"))
        for obj in doc.get("objectives") or []:
            metric = str(obj.get("metric") or "")
            self.assertNotIn(
                metric,
                _PRIMARY_FORBIDDEN,
                f"objective {obj.get('id')} uses win-rate metric {metric}",
            )
            self.assertIn(
                metric,
                _PRIMARY_EXPECTANCY,
                f"objective {obj.get('id')} metric {metric} not in expectancy-first set",
            )

    def test_si_singularity_portfolio_metric(self):
        doc = json.loads((_ROOT / "config" / "si_singularity.json").read_text(encoding="utf-8"))
        pf = doc.get("portfolio") or {}
        self.assertEqual(pf.get("metric"), "combined_rolling_realized_usd")
        self.assertNotIn("win_rate", str(pf.get("metric") or ""))


if __name__ == "__main__":
    unittest.main()
