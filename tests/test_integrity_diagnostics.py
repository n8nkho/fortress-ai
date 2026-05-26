"""Integrity diagnostics for recursive SI."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class TestIntegrityDiagnostics(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name

    def test_detects_duplicate_entries(self):
        p = Path(self._td.name) / "ai_decisions.jsonl"
        rows = []
        for _ in range(5):
            rows.append(
                {
                    "decision": {
                        "action": "enter_position",
                        "parameters": {"symbol": "IBM", "qty": 10},
                    },
                    "act": {"executed": True},
                }
            )
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

        from utils.integrity_diagnostics import scan_unified_agent

        findings = scan_unified_agent()
        codes = [f["code"] for f in findings]
        self.assertIn("duplicate_entry_accumulation", codes)

    def test_skim_adaptive_actions_on_negative_session(self):
        from utils.integrity_diagnostics import skim_adaptive_actions

        actions = skim_adaptive_actions(
            {
                "findings": [{"code": "skim_negative_session", "severity": "medium"}],
            }
        )
        self.assertIn("cooldown_mult", actions)


if __name__ == "__main__":
    unittest.main()
