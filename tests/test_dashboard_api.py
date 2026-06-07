"""Dashboard HTTP API shape checks — uses Flask test client (no live server)."""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
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
        self.assertIn("screener", d)
        self.assertIsInstance(d["screener"], dict)

    def test_comparison_includes_equity_fields(self):
        r = self.client.get("/api/comparison")
        self.assertEqual(r.status_code, 200, r.data)
        d = r.get_json()
        self.assertIn("classic", d)
        self.assertIn("fortress_ai", d)
        self.assertIn("portfolio", d["classic"])
        self.assertIn("portfolio", d["fortress_ai"])
        self.assertIn("equity", d["fortress_ai"])
        self.assertIn("realized_pnl", d["classic"])
        self.assertIn("realized_pnl", d["fortress_ai"])
        self.assertIn("chart", d)
        self.assertIn("realized_usd", d["chart"])

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

    def test_si_capability_review_shape(self):
        r = self.client.get("/api/si/capability-review")
        self.assertEqual(r.status_code, 200, r.data)
        d = r.get_json()
        self.assertIn("latest", d)
        self.assertIn("overrides", d)
        self.assertIn("state", d)
        self.assertIn("objectives", d)
        self.assertIsInstance(d["objectives"], list)
        self.assertGreater(len(d["objectives"]), 0)

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

    def test_skim_status_includes_learned_session_stats(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "state").mkdir()
            (root / "learned").mkdir()
            (root / "state" / "NVDA.json").write_text(
                json.dumps({"symbol": "NVDA", "side": "flat", "last_action": "wait"}),
                encoding="utf-8",
            )
            (root / "learned" / "NVDA.json").write_text(
                json.dumps(
                    {
                        "symbol": "NVDA",
                        "session_date_et": "2026-05-22",
                        "session_stats": {"sum_pnl_usd": -0.19, "exits": 5, "wins": 3, "losses": 2},
                        "params": {"target_mult": 0.95},
                    }
                ),
                encoding="utf-8",
            )
            with patch("utils.skim_swarm_config.swarm_data_dir", return_value=root):
                with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=root):
                    with patch("utils.skim_swarm_config.universe", return_value=["NVDA"]):
                        with patch("utils.skim_swarm_config.dry_run", return_value=False):
                            with patch("utils.skim_swarm_config.instance_name", return_value="test"):
                                with patch(
                                    "agents.skim_swarm.pnl.compute_pnl_summary",
                                    return_value={"daily": {}, "per_symbol_realized": []},
                                ):
                                    with patch(
                                        "agents.skim_swarm.quotes.build_symbol_quotes",
                                        return_value={"NVDA": {"last": 100.0, "side": "flat"}},
                                    ):
                                        r = self.client.get("/api/skim/status")
            self.assertEqual(r.status_code, 200, r.data)
            d = r.get_json()
            row = d["symbol_states"][0]
            self.assertEqual(row["symbol"], "NVDA")
            self.assertEqual(row["learned"]["stats"]["wins"], 3)
            self.assertAlmostEqual(row["realized_usd"], -0.19)


if __name__ == "__main__":
    unittest.main()
