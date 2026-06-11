"""Phase 3.4 — PerformanceMonitor auto-revert on expectancy regression."""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agents.performance_monitor import PerformanceMonitor


class TestPerformanceMonitor(unittest.TestCase):
    def test_reverts_on_expectancy_regression(self):
        with TemporaryDirectory() as td:
            os.environ["FORTRESS_AI_DATA_DIR"] = td
            old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
            outcome = {
                "status": "active",
                "proposal_id": "prop-1",
                "logged_at": old,
                "baseline_expectancy_usd": 0.12,
            }
            (Path(td) / "improvement_outcomes.jsonl").write_text(
                json.dumps(outcome) + "\n",
                encoding="utf-8",
            )
            mon = PerformanceMonitor()
            with patch.object(
                mon,
                "get_performance_since",
                return_value={"rolling_expectancy_usd": 0.02, "pnl_sample_trades": 8},
            ):
                with patch.object(mon, "revert_change", return_value={"proposal_id": "prop-1"}) as rev:
                    hits = mon.monitor_active_changes()
            self.assertEqual(len(hits), 1)
            rev.assert_called_once()

    def test_no_revert_before_monitoring_window(self):
        mon = PerformanceMonitor()
        recent = datetime.now(timezone.utc).isoformat()
        self.assertFalse(
            mon.should_revert(
                {"logged_at": recent, "baseline_expectancy_usd": 0.1, "status": "active"},
            )
        )

    def test_reverts_on_drawdown_breach(self):
        mon = PerformanceMonitor()
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        with patch.object(
            mon,
            "get_performance_since",
            return_value={"rolling_expectancy_usd": 0.05, "max_drawdown_fraction": 0.20},
        ):
            self.assertTrue(
                mon.should_revert({"logged_at": old, "baseline_expectancy_usd": 0.05})
            )

    def test_permissive_overrides_cannot_loosen_revert_triggers(self):
        from agents.performance_monitor import get_revert_trigger

        loosen_attempts = {
            "min_rolling_expectancy_usd": -0.20,
            "expectancy_regression_usd": 0.50,
            "max_drawdown_threshold": 0.50,
            "min_monitoring_days": 30,
        }
        defaults = PerformanceMonitor.REVERT_TRIGGERS
        with patch(
            "utils.si_capability_review.load_overrides",
            return_value={"capabilities": loosen_attempts},
        ):
            self.assertEqual(
                get_revert_trigger("min_rolling_expectancy_usd"),
                defaults["min_rolling_expectancy_usd"],
            )
            self.assertEqual(
                get_revert_trigger("expectancy_regression_usd"),
                defaults["expectancy_regression_usd"],
            )
            self.assertEqual(
                get_revert_trigger("max_drawdown_threshold"),
                defaults["max_drawdown_threshold"],
            )
            self.assertEqual(
                get_revert_trigger("min_monitoring_days"),
                defaults["min_monitoring_days"],
            )

    def test_stricter_operator_values_apply(self):
        from agents.performance_monitor import get_revert_trigger

        stricter = {
            "min_rolling_expectancy_usd": -0.03,
            "expectancy_regression_usd": 0.02,
            "max_drawdown_threshold": 0.10,
            "min_monitoring_days": 5,
        }
        with patch(
            "utils.si_capability_review.load_overrides",
            return_value={"capabilities": stricter},
        ):
            self.assertEqual(get_revert_trigger("min_rolling_expectancy_usd"), -0.03)
            self.assertEqual(get_revert_trigger("expectancy_regression_usd"), 0.02)
            self.assertEqual(get_revert_trigger("max_drawdown_threshold"), 0.10)
            self.assertEqual(get_revert_trigger("min_monitoring_days"), 5)


if __name__ == "__main__":
    unittest.main()
