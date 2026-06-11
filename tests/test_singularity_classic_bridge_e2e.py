"""Phase 3.1 — si_singularity → classic_bridge end-to-end."""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TB_ROOT = Path("/home/ubuntu/trading-bot")


class TestSingularityClassicBridgeE2e(unittest.TestCase):
    def _stub_trading_bot_root(self, td: Path) -> Path:
        tb = td / "trading-bot"
        utils = tb / "utils"
        utils.mkdir(parents=True)
        (tb / "data").mkdir(parents=True)
        (utils / "__init__.py").write_text("", encoding="utf-8")
        for name in ("system_time.py", "si_recommendation_queue.py"):
            src = _TB_ROOT / "utils" / name
            if src.is_file():
                shutil.copy(src, utils / name)
        return tb

    def test_push_surpass_to_classic_queue(self):
        with TemporaryDirectory() as td:
            tb = self._stub_trading_bot_root(Path(td))
            queue_path = tb / "data" / "si_recommendation_queue.json"
            queue_path.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")

            from utils.si_singularity import push_surpass_to_classic

            directives = [
                {
                    "objective_id": "classic_candidate_throughput",
                    "component": "classic_fortress",
                    "action": "surpass_escalate",
                    "detail": "Surpass classic_candidate_throughput: avg_candidates_per_screen=1.2 → aspire 2.0",
                }
            ]
            with patch("utils.classic_bridge.resolve_trading_bot_root", return_value=tb):
                with patch("utils.classic_bridge.classic_rolling_metrics", return_value={"latest_regime": "TRENDING_BULL"}):
                    pushed = push_surpass_to_classic(directives)

            self.assertGreaterEqual(len(pushed), 1)
            doc = json.loads(queue_path.read_text(encoding="utf-8"))
            items = doc.get("items") or []
            self.assertTrue(items)
            codes = {str(i.get("code") or "") for i in items}
            self.assertIn("classic_candidate_throughput", codes)
            self.assertTrue(all(str(i.get("component") or "") == "classic_fortress" for i in items))

    def test_run_singularity_cycle_pushes_classic_when_surpass(self):
        with TemporaryDirectory() as td:
            tb = self._stub_trading_bot_root(Path(td))
            os.environ["FORTRESS_AI_DATA_DIR"] = td
            queue_path = tb / "data" / "si_recommendation_queue.json"
            queue_path.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")

            metrics = {
                "skim_swarm": {
                    "rolling_expectancy_usd": 0.1,
                    "rolling_exits": 10,
                    "rolling_payoff_ratio": 1.3,
                    "sessions": [{"session_date_et": "2026-06-10", "realized_usd": 5.0}],
                },
                "infra_swarm": {
                    "rolling_expectancy_usd": 0.4,
                    "rolling_exits": 8,
                    "sessions": [{"session_date_et": "2026-06-10", "realized_usd": 3.0}],
                },
                "unified_ai": {"rolling_realized_usd": 0, "rolling_exits": 0, "sessions": []},
                "classic_fortress": {
                    "avg_candidates_per_screen": 1.2,
                    "screens_sampled": 5,
                    "rolling_fills": 2,
                    "days_since_last_fill": 4,
                    "sessions": [{"session_date_et": "2026-06-10", "realized_usd": 1.0}],
                },
                "si_meta": {"intervention_success_rate": 0.5},
            }

            from utils.si_singularity import run_singularity_cycle

            with patch("utils.classic_bridge.resolve_trading_bot_root", return_value=tb):
                with patch("utils.classic_bridge.classic_rolling_metrics", return_value=metrics["classic_fortress"]):
                    with patch("utils.si_capability_review.apply_capability_updates", return_value=[]):
                        out = run_singularity_cycle(metrics, [], apply=True)

            self.assertIn(out.get("phase"), ("surpass", "singularity"))
            doc = json.loads(queue_path.read_text(encoding="utf-8"))
            classic_items = [
                i for i in (doc.get("items") or []) if str(i.get("component") or "") == "classic_fortress"
            ]
            if any(str(d.get("component") or "") == "classic_fortress" for d in out.get("directives") or []):
                self.assertTrue(classic_items, "classic_fortress directives should land in Classic queue")
            self.assertGreaterEqual(int(out.get("classic_queue_pushed") or 0), len(classic_items))


if __name__ == "__main__":
    unittest.main()
