"""Session stats reconcile from decisions.jsonl."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.session_reconcile import aggregate_session_from_decisions, reconcile_session_stats


class TestSkimSessionReconcile(unittest.TestCase):
    def test_aggregate_session_from_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "decisions.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-05-22T15:00:00+00:00",
                                "results": [
                                    {
                                        "symbol": "NVDA",
                                        "decision": {"action": "enter_long", "reasoning": "momentum_long score=0.30"},
                                        "act": {"executed": True},
                                        "features": {"side": "flat"},
                                    },
                                    {
                                        "symbol": "NVDA",
                                        "decision": {"action": "exit_position", "reasoning": "skim_target_hit:0.20"},
                                        "act": {"executed": True},
                                        "features": {"unrealized_usd": 0.2, "side": "long"},
                                    },
                                ],
                            }
                        )
                    ]
                ),
                encoding="utf-8",
            )
            with patch("agents.skim_swarm.session_reconcile.swarm_data_dir", return_value=root):
                agg = aggregate_session_from_decisions("2026-05-22")
            self.assertIn("NVDA", agg)
            stats = agg["NVDA"]["session_stats"]
            self.assertEqual(stats["entries"], 1)
            self.assertEqual(stats["exits"], 1)
            self.assertAlmostEqual(stats["sum_pnl_usd"], 0.2)
            self.assertEqual(agg["NVDA"]["pattern_stats"]["momentum_long"]["wins"], 1)

    def test_reconcile_updates_learned(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "learned").mkdir()
            (root / "learned" / "NVDA.json").write_text(
                json.dumps(
                    {
                        "version": 4,
                        "symbol": "NVDA",
                        "session_date_et": "2026-05-22",
                        "session_stats": {"exits": 0, "sum_pnl_usd": 0.0, "wins": 0, "losses": 0},
                        "params": {"pattern_deltas": {}},
                        "pattern_stats": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "decisions.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-05-22T15:00:00+00:00",
                        "results": [
                            {
                                "symbol": "NVDA",
                                "decision": {"action": "enter_long", "reasoning": "momentum_long score=0.30"},
                                "act": {"executed": True},
                                "features": {},
                            },
                            {
                                "symbol": "NVDA",
                                "decision": {"action": "exit_position", "reasoning": "stop_loss:-0.30"},
                                "act": {"executed": True},
                                "features": {"unrealized_usd": -0.3, "side": "long"},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("agents.skim_swarm.session_reconcile.swarm_data_dir", return_value=root):
                with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=root):
                    with patch("agents.skim_swarm.session_reconcile.universe", return_value=["NVDA"]):
                        with patch("agents.skim_swarm.session_reconcile.session_date_et", return_value="2026-05-22"):
                            with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                                report = reconcile_session_stats(force=True)
            self.assertTrue(report["ok"])
            self.assertEqual(report["symbols_updated"], 1)
            data = json.loads((root / "learned" / "NVDA.json").read_text())
            self.assertEqual(data["session_stats"]["exits"], 1)
            self.assertAlmostEqual(data["session_stats"]["sum_pnl_usd"], -0.3)


if __name__ == "__main__":
    unittest.main()
