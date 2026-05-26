"""Deployed fix tracking for integrity false-positive suppression."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestSiFixDeployment(unittest.TestCase):
    def test_code_guard_present_for_exit_notional(self):
        from utils.si_fix_deployment import code_guard_present_in_repo

        self.assertTrue(code_guard_present_in_repo("exit_notional_blocked"))

    def test_sync_records_deployed_fixes(self):
        import os

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = td.name
        from utils.si_fix_deployment import is_deployed, sync_deployed_from_registry

        synced = sync_deployed_from_registry()
        self.assertTrue(synced or is_deployed("exit_notional_blocked"))


if __name__ == "__main__":
    unittest.main()
