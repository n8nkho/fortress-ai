"""Skim swarm P&L summary."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.pnl import compute_pnl_summary


class TestSkimPnl(unittest.TestCase):
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
            (root / "decisions.jsonl").write_text("", encoding="utf-8")
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
            self.assertEqual(out["cumulative"]["realized_usd"], 1.5)


if __name__ == "__main__":
    unittest.main()
