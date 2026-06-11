"""Phase 1 — protected paths deny-list and integrity guard for autonomous code SI."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_code_implementation import (
    RunLockBusy,
    _diff_allowed,
    _restore_protected_snapshots,
    _run_lock,
    _snapshot_protected_files,
    _verify_protected_integrity,
    implement_item,
    path_is_protected,
    run_autonomous_code_si_cycle,
)


class TestSiProtectedPaths(unittest.TestCase):
    def test_path_is_protected(self):
        self.assertTrue(path_is_protected("utils/pre_trade_gate.py"))
        self.assertTrue(path_is_protected("config/si_capability_registry.json"))
        self.assertFalse(path_is_protected("utils/edge_autofix.py"))

    def test_diff_allowed_blocks_protected_path(self):
        ok, reason = _diff_allowed(["utils/pre_trade_gate.py"])
        self.assertFalse(ok)
        self.assertIn("SI-BLOCKED", reason)
        self.assertIn("protected_path", reason)

    def test_diff_allowed_permits_non_protected_utils(self):
        ok, reason = _diff_allowed(["utils/edge_autofix.py"])
        self.assertTrue(ok, reason)

    def test_integrity_guard_detects_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            gate = repo / "utils" / "pre_trade_gate.py"
            gate.parent.mkdir(parents=True)
            gate.write_text("original", encoding="utf-8")
            snap = _snapshot_protected_files(repo)
            gate.write_text("tampered", encoding="utf-8")
            modified = _verify_protected_integrity(repo, snap)
            self.assertIn("utils/pre_trade_gate.py", modified)
            _restore_protected_snapshots(repo, snap)
            self.assertEqual(gate.read_text(encoding="utf-8"), "original")

    def test_dry_run_protected_probe_blocks_gate(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["FORTRESS_AI_DATA_DIR"] = td
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

            with patch("utils.si_code_implementation._implementation_attempts_today", return_value=0):
                result = implement_item(item["id"], dry_run=True)
            probe = result.get("protected_probe") or {}
            self.assertFalse(probe.get("allowed"))
            self.assertIn("SI-BLOCKED", probe.get("block") or "")

    def test_run_cycle_frozen_when_halted(self):
        with patch("utils.operator_halt.is_trading_halted", return_value=True):
            out = run_autonomous_code_si_cycle()
        self.assertEqual(out.get("skipped"), "SI-FROZEN: trading_halted")

    def test_run_lock_prevents_concurrent_entry(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["FORTRESS_AI_DATA_DIR"] = td
            with _run_lock():
                with self.assertRaises(RunLockBusy):
                    with _run_lock():
                        pass


if __name__ == "__main__":
    unittest.main()
