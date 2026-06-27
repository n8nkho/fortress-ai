"""Tests for RSI-only auto git deploy allowlist."""
from __future__ import annotations

import unittest
from pathlib import Path


class TestSiRsiAutoDeploy(unittest.TestCase):
    def test_fortress_rsi_paths(self):
        from utils.si_rsi_auto_deploy import all_paths_rsi_deployable, is_rsi_deploy_path

        repo = Path("/home/ubuntu/fortress-ai")
        self.assertTrue(is_rsi_deploy_path("utils/unified_position_exit.py", repo=repo))
        self.assertTrue(
            all_paths_rsi_deployable(
                ["data/tunable_params.json", "utils/tunable_overrides.py"],
                repo=repo,
            )
        )
        self.assertFalse(
            all_paths_rsi_deployable(
                ["utils/pre_trade_gate.py", "data/tunable_params.json"],
                repo=repo,
            )
        )

    def test_trading_bot_rsi_paths(self):
        from utils.si_rsi_auto_deploy import all_paths_rsi_deployable, is_rsi_deploy_path

        repo = Path("/home/ubuntu/trading-bot")
        self.assertTrue(is_rsi_deploy_path("utils/classic_si_screener.py", repo=repo))
        self.assertTrue(
            all_paths_rsi_deployable(
                ["utils/adaptive_rsi.py", "tests/test_adaptive_rsi.py"],
                repo=repo,
            )
        )


if __name__ == "__main__":
    unittest.main()
