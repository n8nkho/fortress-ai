"""Cross-stack items must never reach auto-implement or auto-resolve dispositions."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import utils.si_recommendation_queue as sq
from utils.si_code_implementation import auto_assess_item, auto_promote_pending_human_go


def _cross_stack_finding(**overrides):
    base = {
        "code": "classic_mirror_test",
        "component": "classic_fortress",
        "title": "Cross-stack mirror",
        "recommendation": "Review Classic finding for Fortress applicability.",
        "severity": "medium",
        "kind": "code_guard",
    }
    base.update(overrides)
    return base


class TestCrossStackNeverAutoApplies(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.td = Path(self._tmpdir.name)
        self._patch_queue = mock.patch.object(sq, "queue_path", return_value=self.td / "q.json")
        self._patch_data = mock.patch.object(sq, "_data_dir", return_value=self.td)
        self._patch_queue.start()
        self._patch_data.start()

    def tearDown(self):
        self._patch_data.stop()
        self._patch_queue.stop()
        self._tmpdir.cleanup()

    def _upsert(self, source: str = "cross_stack_belief") -> dict:
        return sq.upsert_from_finding(_cross_stack_finding(), source=source)

    def _reload(self, item_id: str) -> dict:
        return next(x for x in sq.load_queue()["items"] if x["id"] == item_id)

    def _assert_not_forbidden(self, item: dict, *, context: str) -> None:
        disp = str(item.get("disposition") or "")
        self.assertNotIn(
            disp,
            sq.CROSS_STACK_FORBIDDEN_AUTO_DISPOSITIONS,
            f"{context}: disposition={disp}",
        )

    def test_set_agent_assessment_never_auto_queues(self):
        item = self._upsert()
        with mock.patch.dict(os.environ, {"FORTRESS_SI_AUTO_CODE": "1"}, clear=False):
            updated = sq.set_agent_assessment(item["id"], worth_implementing=True, rationale="test")
        self.assertEqual(updated["disposition"], sq.DISPOSITION_PENDING_HUMAN)
        self.assertTrue(updated.get("requires_human_go"))
        self._assert_not_forbidden(updated, context="set_agent_assessment")

    def test_auto_assess_item_routes_to_human_go(self):
        item = self._upsert(source="fortress_ai_belief")
        with mock.patch.dict(os.environ, {"FORTRESS_SI_AUTO_CODE": "1"}, clear=False):
            assessed = auto_assess_item(item["id"])
        self.assertEqual(assessed["disposition"], sq.DISPOSITION_PENDING_HUMAN)
        self.assertTrue(assessed.get("requires_human_go"))
        self._assert_not_forbidden(assessed, context="auto_assess_item")

    def test_auto_promote_pending_human_go_skips_cross_stack(self):
        item = self._upsert()
        with mock.patch.dict(
            os.environ,
            {"FORTRESS_SI_AUTO_CODE": "1", "FORTRESS_SI_AUTO_APPROVE": "1"},
            clear=False,
        ):
            sq.set_agent_assessment(item["id"], worth_implementing=True, rationale="test")
            promoted = auto_promote_pending_human_go(limit=5)
        self.assertEqual(promoted, [])
        reloaded = self._reload(item["id"])
        self._assert_not_forbidden(reloaded, context="auto_promote_pending_human_go")
        self.assertEqual(reloaded["disposition"], sq.DISPOSITION_PENDING_HUMAN)

    def test_reconcile_cleared_findings_skips_cross_stack(self):
        item = self._upsert()
        sq.reconcile_cleared_findings({"findings": []})
        reloaded = self._reload(item["id"])
        self.assertEqual(reloaded["status"], sq.STATUS_OPEN)
        self._assert_not_forbidden(reloaded, context="reconcile_cleared_findings")

    def test_reconcile_deployed_guards_skips_cross_stack(self):
        item = self._upsert()
        sq.reconcile_deployed_guards({"findings": []})
        reloaded = self._reload(item["id"])
        self.assertEqual(reloaded["status"], sq.STATUS_OPEN)
        self._assert_not_forbidden(reloaded, context="reconcile_deployed_guards")


if __name__ == "__main__":
    unittest.main()
