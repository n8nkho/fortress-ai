"""Skim swarm P&L summary."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.pnl import compute_pnl_summary, learned_symbol_snapshot


class TestSkimPnl(unittest.TestCase):
    def test_learned_symbol_snapshot_uses_session_stats(self):
        snap = learned_symbol_snapshot(
            {
                "params": {"target_mult": 0.9, "enter_long_delta": 0.02},
                "session_stats": {"sum_pnl_usd": 0.43, "exits": 3, "wins": 3, "losses": 0},
            }
        )
        self.assertEqual(snap["stats"]["wins"], 3)
        self.assertAlmostEqual(snap["realized_usd"], 0.43)
        self.assertEqual(snap["target_mult"], 0.9)

    def test_compute_pnl_summary_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            learned = root / "learned"
            learned.mkdir()
            (learned / "SOXX.json").write_text(
                json.dumps(
                    {
                        "symbol": "SOXX",
                        "session_date_et": "2026-05-22",
                        "session_stats": {"sum_pnl_usd": 1.5, "exits": 2, "wins": 2, "losses": 0},
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
                                "symbol": "SOXX",
                                "decision": {"action": "exit_position"},
                                "act": {"executed": True},
                                "features": {"unrealized_usd": 1.5},
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("agents.skim_swarm.pnl.swarm_data_dir", return_value=root):
                with patch("agents.skim_swarm.pnl.session_date_et", return_value="2026-05-22"):
                    with patch("agents.skim_swarm.pnl.load_swarm_state", return_value={"day_realized_pnl": 0.5}):
                        with patch(
                            "agents.skim_swarm.pnl.observe_account",
                            return_value={
                                "positions": {
                                    "PLTR": {
                                        "symbol": "PLTR",
                                        "side": "long",
                                        "qty": 1,
                                        "unrealized_pl": 0.25,
                                    }
                                }
                            },
                        ):
                            with patch("agents.skim_swarm.pnl.universe", return_value=["SOXX", "PLTR"]):
                                out = compute_pnl_summary()
            self.assertIn("daily", out)
            self.assertIn("cumulative", out)
            self.assertEqual(out["daily"]["unrealized_usd"], 0.25)
            self.assertAlmostEqual(out["cumulative"]["realized_usd"], 1.5)
            self.assertEqual(out["session_learned_realized_usd"], 1.5)


if __name__ == "__main__":
    unittest.main()
