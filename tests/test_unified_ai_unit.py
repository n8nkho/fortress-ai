"""Unit tests — no network keys required."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
import tempfile

import sys

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from agents.unified_ai_agent import _parse_llm_json  # noqa: E402
from utils.api_costs import append_llm_cost_record, estimate_llm_cost_usd, week_cost_usd  # noqa: E402


class TestParse(unittest.TestCase):
    def test_plain_json(self):
        d = _parse_llm_json('{"action":"wait","confidence":0.5}')
        self.assertEqual(d["action"], "wait")

    def test_fenced_json(self):
        raw = """Here:\n```json\n{\"action\":\"wait\",\"confidence\":0.9}\n```"""
        d = _parse_llm_json(raw)
        self.assertEqual(d["confidence"], 0.9)


class TestCosts(unittest.TestCase):
    def test_estimate_positive(self):
        c = estimate_llm_cost_usd("deepseek-chat", 1_000_000, 500_000)
        self.assertGreater(c, 0)


class TestWeeklyLedger(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        import os

        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name

    def test_week_cost_append(self):
        append_llm_cost_record(model="deepseek-chat", input_tokens=1000, output_tokens=500, cost_usd=0.001)
        total, _, _ = week_cost_usd()
        self.assertGreaterEqual(total, 0.001)


if __name__ == "__main__":
    unittest.main()
