"""Weekly LLM budget degrade vs stop."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from utils.api_costs import weekly_budget_exceeded, weekly_llm_budget_status


class TestWeeklyBudget(unittest.TestCase):
    def test_degrade_when_exceeded_default_mode(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ai_llm_cost_ledger.jsonl"
            with patch.dict(
                os.environ,
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_AI_WEEKLY_COST_CAP_USD": "0.01",
                    "FORTRESS_AI_WEEKLY_BUDGET_MODE": "degrade",
                },
            ):
                ledger.write_text(
                    '{"timestamp":"'
                    + datetime.now(timezone.utc).isoformat()
                    + '","cost_usd":0.05}\n',
                    encoding="utf-8",
                )
                st = weekly_llm_budget_status()
                self.assertTrue(st["exceeded"])
                self.assertFalse(st["should_stop_loop"])
                self.assertTrue(st["should_degrade_llm"])
                stop, _, _ = weekly_budget_exceeded()
                self.assertFalse(stop)

    def test_stop_when_mode_stop(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ai_llm_cost_ledger.jsonl"
            with patch.dict(
                os.environ,
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_AI_WEEKLY_COST_CAP_USD": "0.01",
                    "FORTRESS_AI_WEEKLY_BUDGET_MODE": "stop",
                },
            ):
                ledger.write_text(
                    '{"timestamp":"'
                    + datetime.now(timezone.utc).isoformat()
                    + '","cost_usd":0.05}\n',
                    encoding="utf-8",
                )
                st = weekly_llm_budget_status()
                self.assertTrue(st["should_stop_loop"])
                stop, _, _ = weekly_budget_exceeded()
                self.assertTrue(stop)


if __name__ == "__main__":
    unittest.main()
