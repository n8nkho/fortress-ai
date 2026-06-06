"""Tests for swarm decisions PnL and skim pattern review."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.skim_pattern_review import apply_swarm_pattern_review, swarm_winning_pattern_share
from utils.swarm_decisions_pnl import cumulative_realized_from_decisions, daily_realized_from_decisions


class TestSwarmDecisionsPnl(unittest.TestCase):
    def test_cumulative_from_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "decisions.jsonl"
            wave = {
                "ts": "2026-06-01T14:00:00+00:00",
                "results": [
                    {
                        "symbol": "SPY",
                        "decision": {"action": "exit_position"},
                        "act": {"executed": True},
                        "features": {"unrealized_usd": -1.25},
                    },
                    {
                        "symbol": "QQQ",
                        "decision": {"action": "exit_position"},
                        "act": {"executed": True},
                        "features": {"unrealized_usd": 0.75},
                    },
                ],
            }
            p.write_text(json.dumps(wave) + "\n", encoding="utf-8")
            day_total, day_n = daily_realized_from_decisions(p, "2026-06-01")
            cum_total, cum_n = cumulative_realized_from_decisions(p)
            self.assertEqual(day_n, 2)
            self.assertAlmostEqual(day_total, -0.5)
            self.assertEqual(cum_n, 2)
            self.assertAlmostEqual(cum_total, -0.5)


class TestSkimPatternReview(unittest.TestCase):
    def test_swarm_winning_pattern_share_from_lifetime(self):
        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "learned"
            learned.mkdir()
            (learned / "SPY.json").write_text(
                json.dumps(
                    {
                        "symbol": "SPY",
                        "params": {"disable_patterns": []},
                        "lifetime_pattern_stats": {
                            "momentum_long": {"exits": 4, "wins": 3, "losses": 1, "sum_pnl_usd": 1.2},
                            "rip_fade": {"exits": 3, "wins": 1, "losses": 2, "sum_pnl_usd": -0.4},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("utils.skim_pattern_review._learned_dir", return_value=learned):
                share = swarm_winning_pattern_share(min_exits=3)
            self.assertIsNotNone(share)
            self.assertAlmostEqual(share, 0.5)


if __name__ == "__main__":
    unittest.main()
