"""Tests for operator status snapshots."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestOperatorStatusReport(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name
        os.environ["FORTRESS_ADAPTIVE_MAX_OPEN"] = "0"

    def test_persist_writes_latest_and_jsonl(self):
        from utils.operator_status_report import persist_operator_status

        with patch("utils.operator_status_report._service_active", return_value="active"):
            with patch("utils.operator_status_report.build_operator_status") as mock_build:
                mock_build.return_value = {"ts": "2026-06-15T12:00:00-04:00", "services": {}}
                doc = persist_operator_status()
        self.assertEqual(doc["ts"], "2026-06-15T12:00:00-04:00")
        latest = Path(self._td.name) / "operator_status" / "latest.json"
        self.assertTrue(latest.is_file())
        jsonl = Path(self._td.name) / "operator_status" / "reports.jsonl"
        self.assertTrue(jsonl.is_file())
        self.assertIn("2026-06-15", jsonl.read_text(encoding="utf-8"))

    def test_markdown_summary(self):
        from utils.operator_status_report import format_operator_status_markdown

        md = format_operator_status_markdown(
            {
                "ts": "2026-06-15T12:00:00-04:00",
                "system_tz": "America/New_York",
                "services": {"fortress-ai-skim-swarm": "active"},
                "portfolio": {"open_positions": 3, "skim_max_open_effective": 10, "infra_max_open_effective": 10},
                "skim": {"pnl": {"daily": {"realized_usd": 0.5, "exit_count": 2}}},
                "infra": {"pnl": {"daily": {"realized_usd": 0.1, "exit_count": 1}}},
                "si_queue": {"pending_agent_review": 1, "pending_human_go": 0, "auto_implement_queued": 0},
                "auto_code": {"enabled": True, "cursor_cli": {"ok": True}},
                "anomalies": [],
            }
        )
        self.assertIn("Operator status", md)
        self.assertIn("cursor=OK", md)


if __name__ == "__main__":
    unittest.main()
