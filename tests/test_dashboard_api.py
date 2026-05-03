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


if __name__ == "__main__":
    unittest.main()
