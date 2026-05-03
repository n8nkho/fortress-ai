"""Dashboard HTTP API shape checks — uses Flask test client (no live server)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestDashboardApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard.ai_command_center as acc

        cls.app = acc.app
        cls.client = cls.app.test_client()

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d.get("ok"))

    def test_index_no_cache_and_build(self):
        import dashboard.ai_command_center as acc

        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r.headers.get("Cache-Control", ""))
        self.assertIn(acc._DASHBOARD_UI_BUILD.encode("utf-8"), r.data)

    def test_charts_dashboard_shape(self):
        r = self.client.get("/api/charts/dashboard")
        self.assertEqual(r.status_code, 200, r.data)
        d = r.get_json()
        self.assertIn("spy", d)
        self.assertIn("llm_cost", d)
        spy = d["spy"]
        self.assertTrue(
            {"change_pct", "labels", "prices"}.issubset(spy.keys()),
            msg=f"spy keys: {spy.keys()}",
        )
        lc = d["llm_cost"]
        labels = lc.get("labels") or []
        self.assertEqual(len(labels), 14)
        self.assertIsInstance(spy.get("labels"), list)
        self.assertIsInstance(spy.get("prices"), list)

    def test_expert_bundle_shape(self):
        r = self.client.get("/api/expert/bundle")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("cost_ledger_tail", d)
        self.assertIsInstance(d["cost_ledger_tail"], list)
        self.assertIn("prompt_note", d)

    def test_self_improvement_status_shape(self):
        r = self.client.get("/api/self_improvement/status")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("immutable", d)
        self.assertIn("current", d)
        self.assertIn("velocity", d)

    def test_self_improvement_proposals_shape(self):
        r = self.client.get("/api/self_improvement/proposals")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("proposals", d)
        self.assertIsInstance(d["proposals"], list)
        self.assertIn("total_lines", d)

    def test_prompt_evolution_status_shape(self):
        r = self.client.get("/api/prompt_evolution/status")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d.get("tier"), 2)
        self.assertIn("limits", d)

    def test_governance_tiers_shape(self):
        r = self.client.get("/api/governance/tiers")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("tier_0_params", d)
        self.assertIn("tier_3_immutable_names", d)

    def test_governance_pending_ok(self):
        r = self.client.get("/api/governance/pending")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("veto_pending", d)


if __name__ == "__main__":
    unittest.main()
