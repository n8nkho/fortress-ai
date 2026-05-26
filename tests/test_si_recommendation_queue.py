"""SI recommendation queue tests."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class TestSiRecommendationQueue(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name

    def test_upsert_creates_pending_agent_item(self):
        from utils.si_recommendation_queue import (
            DISPOSITION_PENDING_AGENT,
            load_queue,
            upsert_from_finding,
        )

        item = upsert_from_finding(
            {
                "code": "weekly_llm_budget_tight",
                "severity": "medium",
                "component": "unified_ai",
                "recommendation": "Raise budget cap.",
            }
        )
        self.assertEqual(item.get("disposition"), DISPOSITION_PENDING_AGENT)
        q = load_queue()
        self.assertEqual(len(q.get("items") or []), 1)

    def test_agent_assessment_moves_to_human_go(self):
        from utils.si_recommendation_queue import (
            DISPOSITION_PENDING_HUMAN,
            set_agent_assessment,
            upsert_from_finding,
        )

        item = upsert_from_finding(
            {
                "code": "weekly_llm_budget_tight",
                "severity": "medium",
                "component": "unified_ai",
                "recommendation": "Raise budget cap.",
            }
        )
        updated = set_agent_assessment(
            item["id"],
            worth_implementing=True,
            rationale="Budget is too tight for SI cycles.",
            proposed_implementation="Increase FORTRESS_AI_WEEKLY_LLM_CAP_USD to 10.",
        )
        self.assertEqual(updated.get("disposition"), DISPOSITION_PENDING_HUMAN)

    def test_register_fix_in_registry(self):
        from utils.si_recommendation_queue import fix_registry_path, load_fix_registry, register_fix_in_registry

        register_fix_in_registry(
            code="test_fix",
            title="Test fix",
            recommendation="Do the thing.",
        )
        reg = load_fix_registry()
        self.assertIn("test_fix", reg.get("fixes") or {})
        self.assertTrue(fix_registry_path().exists())


if __name__ == "__main__":
    unittest.main()
