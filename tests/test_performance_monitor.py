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


if __name__ == "__main__":
    unittest.main()
