"""Dashboard HTTP API shape checks — uses Flask test client (no live server)."""
from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


_BASIC_KEYS = (
    "FORTRESS_AI_DASHBOARD_BASIC_USER",
    "FORTRESS_AI_DASHBOARD_BASIC_PASSWORD",
    "FORTRESS_AI_DASHBOARD_BASIC_PASS",
)


class TestDashboardApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Dashboard import runs load_fortress_dotenv() and may re-fill Basic auth from .env.
        cls._basic_backup = {k: os.environ[k] for k in _BASIC_KEYS if k in os.environ}
        import dashboard.ai_command_center as acc

        for k in _BASIC_KEYS:
            os.environ.pop(k, None)

        cls.app = acc.app
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for k in _BASIC_KEYS:
            os.environ.pop(k, None)
        for k, v in cls._basic_backup.items():
            os.environ[k] = v

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d.get("ok"))

    def test_dashboard_belief_memory_endpoint(self):
        for path in ("/api/dashboard/belief_memory", "/api/ai/belief_memory"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, (path, r.data))
            d = r.get_json()
            self.assertIn("total_beliefs", d)

    def test_dashboard_ingest_health_endpoint(self):
        for path in ("/api/dashboard/ingest_health", "/api/ai/ingest_health"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, (path, r.data))
            d = r.get_json()
            self.assertTrue("_missing" in d or "sources" in d or "last_run" in d)

    def test_alpaca_diagnose_shape(self):
        r = self.client.get("/api/alpaca/diagnose")
        self.assertEqual(r.status_code, 200, r.data)
        d = r.get_json()
        self.assertIn("connected", d)
        self.assertIn("sdk_installed", d)
        self.assertIn("keys_configured", d)
        self.assertIn("paper_mode", d)
        if d.get("connected"):
            self.assertIn(d.get("positions_fetch"), ("ok", "error"))

    def test_current_state_has_domain_intel(self):
        r = self.client.get("/api/ai/current_state")
        self.assertEqual(r.status_code, 200, r.data)
        d = r.get_json()
        self.assertIn("domain_intel", d)
        self.assertIsInstance(d["domain_intel"], dict)
        self.assertIn("schema_version", d["domain_intel"])
        self.assertIn("belief_memory", d)
        self.assertIsInstance(d["belief_memory"], dict)
        self.assertIn("ingest_health", d)
        self.assertIsInstance(d["ingest_health"], dict)

    def test_build_endpoint_shape(self):
        r = self.client.get("/api/build")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn("ui_build", d)
        self.assertTrue(d.get("template_has_ops_panels_hint"))

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

    def test_optional_basic_auth_blocks_anonymous(self):
        with patch.dict(
            os.environ,
            {
                "FORTRESS_AI_DASHBOARD_BASIC_USER": "alice",
                "FORTRESS_AI_DASHBOARD_BASIC_PASSWORD": "s3cret!",
            },
        ):
            r = self.client.get("/api/health")
            self.assertEqual(r.status_code, 401)

    def test_optional_basic_auth_allows_with_header(self):
        hdr = "Basic " + base64.b64encode(b"alice:s3cret!").decode("ascii")
        with patch.dict(
            os.environ,
            {
                "FORTRESS_AI_DASHBOARD_BASIC_USER": "alice",
                "FORTRESS_AI_DASHBOARD_BASIC_PASSWORD": "s3cret!",
            },
        ):
            r = self.client.get("/api/health", headers={"Authorization": hdr})
            self.assertEqual(r.status_code, 200)

    def test_optional_basic_auth_exempt_health(self):
        with patch.dict(
            os.environ,
            {
                "FORTRESS_AI_DASHBOARD_BASIC_USER": "alice",
                "FORTRESS_AI_DASHBOARD_BASIC_PASSWORD": "s3cret!",
                "FORTRESS_AI_DASHBOARD_AUTH_EXEMPT_HEALTH": "1",
            },
        ):
            r = self.client.get("/api/health")
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
