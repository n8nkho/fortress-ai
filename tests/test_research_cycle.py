"""Tests for research cycle hypothesis promote/kill."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.research_cycle import (
    evaluate_anticipation_hypothesis,
    load_entry_snapshots,
    run_research_cycle,
)


class TestResearchCycle(unittest.TestCase):
    def _write_jsonl(self, path: Path, waves: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(w) for w in waves) + "\n", encoding="utf-8")

    def test_evaluate_chop_hypothesis_blocks_losers(self):
        entries = [
            {
                "side": "long",
                "exit_pnl": -0.25,
                "features": {
                    "r1m": 0.0001,
                    "r3m": 0.0002,
                    "r5m": 0.0001,
                    "rsi1m": 50,
                    "spy_r5m": 0.0,
                },
            },
            {
                "side": "short",
                "exit_pnl": 0.15,
                "features": {
                    "r1m": 0.0001,
                    "r3m": 0.0002,
                    "r5m": 0.0001,
                    "rsi1m": 50,
                    "spy_r5m": 0.0,
                },
            },
        ]
        ev = evaluate_anticipation_hypothesis(
            "chop_no_edge",
            entries,
            tests={"min_blocked_loser_pnl_usd": 0.2, "max_blocked_winner_pnl_usd": 0.10},
        )
        self.assertGreater(ev["blocked_loser_pnl_usd"], 0)
        self.assertFalse(ev["passes"])  # winner cost exceeds max

    def test_load_entry_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "decisions.jsonl"
            self._write_jsonl(
                p,
                [
                    {
                        "ts": "2026-05-26T14:00:00+00:00",
                        "results": [
                            {
                                "symbol": "SPY",
                                "features": {"r1m": 0.001, "r5m": 0.002, "side": "flat"},
                                "decision": {
                                    "action": "enter_long",
                                    "score": 0.35,
                                    "reasoning": "pullback_uptrend score=0.35",
                                },
                                "act": {"executed": True},
                            },
                            {
                                "symbol": "SPY",
                                "features": {"unrealized_usd": -0.2, "side": "long"},
                                "decision": {"action": "exit_position", "reasoning": "stop_loss:-0.200"},
                                "act": {"executed": True},
                            },
                        ],
                    }
                ],
            )
            snaps = load_entry_snapshots(p, max_sessions=5)
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["symbol"], "SPY")
            self.assertAlmostEqual(snaps[0]["exit_pnl"], -0.2)

    def test_run_research_cycle_writes_state(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            decisions = data / "decisions.jsonl"
            self._write_jsonl(
                decisions,
                [
                    {
                        "ts": "2026-05-26T14:00:00+00:00",
                        "results": [
                            {
                                "symbol": "SPY",
                                "features": {"r1m": 0.0001, "r5m": 0.0001, "rsi1m": 50, "side": "flat"},
                                "decision": {
                                    "action": "enter_long",
                                    "score": 0.35,
                                    "reasoning": "pullback_uptrend score=0.35",
                                },
                                "act": {"executed": True},
                                "learned_params": {"target_mult": 1.0},
                            },
                            {
                                "symbol": "SPY",
                                "features": {"unrealized_usd": -0.3, "side": "long"},
                                "decision": {"action": "exit_position", "reasoning": "stop_loss:-0.300"},
                                "act": {"executed": True},
                            },
                        ],
                    }
                ],
            )
            with patch("utils.skim_swarm_config.swarm_data_dir", return_value=data):
                with patch("utils.movement_anticipation.research_state_path") as rsp:
                    with patch("utils.research_cycle.save_hypothesis_registry"):
                        rsp.return_value = data / "research_state.json"
                        report = run_research_cycle(
                            component="skim_swarm",
                            max_sessions=5,
                            decisions_path=decisions,
                        )
            self.assertTrue(report.get("ok"))
            self.assertTrue((data / "research_cycle_report.json").exists())


if __name__ == "__main__":
    unittest.main()
