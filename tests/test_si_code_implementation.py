"""Autonomous code SI — assess and implement without human go."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_code_implementation import (
    auto_assess_item,
    auto_code_enabled,
    build_implementation_prompt,
    can_auto_implement,
    run_autonomous_code_si_cycle,
)


class TestSiCodeImplementation(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name
        os.environ["FORTRESS_SI_AUTO_CODE"] = "1"

    def test_auto_code_enabled_default(self):
        os.environ.pop("FORTRESS_SI_AUTO_CODE", None)
        self.assertTrue(auto_code_enabled())

    def test_heuristic_assess_queues_implement(self):
        from utils.si_recommendation_queue import (
            DISPOSITION_AUTO_IMPLEMENT_QUEUED,
            upsert_from_finding,
        )

        item = upsert_from_finding(
            {
                "code": "test_code_guard",
                "severity": "high",
                "component": "unified_ai",
                "recommendation": "Add guard in unified_ai_agent.",
            }
        )
        with patch("utils.si_code_implementation._llm_assessment", return_value=None):
            updated = auto_assess_item(item["id"])
        self.assertEqual(updated.get("disposition"), DISPOSITION_AUTO_IMPLEMENT_QUEUED)
        self.assertTrue(updated.get("agent_assessment", {}).get("worth_implementing"))

    def test_can_auto_implement_monitor_blocked(self):
        item = {"status": "open", "kind": "monitor", "disposition": "auto_implement_queued"}
        ok, reason = can_auto_implement(item)
        self.assertFalse(ok)
        self.assertEqual(reason, "monitor_only")

    def test_build_prompt_includes_constraints(self):
        prompt = build_implementation_prompt(
            {
                "id": "x",
                "code": "duplicate_entry_accumulation",
                "title": "Dup entry",
                "component": "unified_ai",
                "impact": "critical",
                "agent_assessment": {"proposed_implementation": "Block re-entry."},
            }
        )
        self.assertIn("pre-trade gate", prompt)
        self.assertIn("fortress-ai", prompt)

    def test_run_cycle_skipped_when_disabled(self):
        os.environ["FORTRESS_SI_AUTO_CODE"] = "0"
        out = run_autonomous_code_si_cycle()
        self.assertEqual(out.get("skipped"), "auto_code_disabled")

    def test_cursor_agent_resolves_local_bin(self):
        from utils.si_code_implementation import _cursor_agent_argv

        fake = Path(self._td.name) / "cursor-agent"
        fake.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"FORTRESS_SI_CURSOR_BIN": str(fake), "FORTRESS_SI_CURSOR_TRUST": "1"}):
            argv = _cursor_agent_argv("hello")
        self.assertEqual(argv[0], str(fake))
        self.assertIn("--trust", argv)
        self.assertIn("--print", argv)

    def test_cursor_agent_resolved_probe(self):
        from utils.si_code_implementation import cursor_agent_resolved

        with patch(
            "utils.si_code_implementation._cursor_agent_argv",
            return_value=["/home/ubuntu/.local/bin/cursor-agent", "--trust", "--print", "probe"],
        ):
            doc = cursor_agent_resolved()
        self.assertTrue(doc.get("ok"))

    def test_implement_dry_run(self):
        from utils.si_recommendation_queue import upsert_from_finding

        item = upsert_from_finding(
            {
                "code": "test_guard",
                "severity": "medium",
                "component": "skim_swarm",
                "recommendation": "Fix thing.",
            }
        )
        item["disposition"] = "auto_implement_queued"
        item["agent_assessment"] = {
            "worth_implementing": True,
            "proposed_implementation": "Edit signal.py",
        }
        from utils.si_recommendation_queue import load_queue, save_queue

        q = load_queue()
        q["items"][-1] = item
        save_queue(q)

        from utils.si_code_implementation import implement_item

        with patch("utils.si_code_implementation._implementation_attempts_today", return_value=0):
            result = implement_item(item["id"], dry_run=True)
        self.assertTrue(result.get("dry_run"))
        self.assertTrue(Path(result["prompt_path"]).is_file())

    def test_velocity_cap_counts_failed_attempts(self):
        from utils.si_code_implementation import _record_implementation_attempt, max_implementations_per_day

        os.environ["FORTRESS_SI_AUTO_CODE_MAX_PER_DAY"] = "1"
        _record_implementation_attempt("attempt-a")
        item = {
            "status": "open",
            "kind": "code_guard",
            "disposition": "auto_implement_queued",
            "agent_assessment": {"worth_implementing": True},
            "code": "x",
        }
        ok, reason = can_auto_implement(item)
        self.assertFalse(ok)
        self.assertEqual(reason, "daily_velocity_cap")

    def test_implement_frozen_when_halted(self):
        from utils.si_recommendation_queue import upsert_from_finding

        item = upsert_from_finding(
            {
                "code": "test_guard",
                "severity": "medium",
                "component": "skim_swarm",
                "recommendation": "Fix thing.",
            }
        )
        with patch("utils.operator_halt.is_trading_halted", return_value=True):
            from utils.si_code_implementation import implement_item

            out = implement_item(item["id"])
        self.assertEqual(out.get("skipped"), "SI-FROZEN: trading_halted")


if __name__ == "__main__":
    unittest.main()
