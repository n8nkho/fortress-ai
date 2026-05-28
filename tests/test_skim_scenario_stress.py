"""Tests for skim scenario stress replay."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents.skim_swarm.scenario_stress import (
    TradeRecord,
    apply_scenario_stress_to_learned,
    entry_would_fire,
    load_trades_from_decisions,
    score_overlay,
    stress_symbol,
    stress_universe,
)


class TestSkimScenarioStress(unittest.TestCase):
    def _write_jsonl(self, path: Path, waves: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(w) for w in waves) + "\n", encoding="utf-8")

    def test_load_trades_pairs_entry_exit(self):
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
                                "features": {"unrealized_usd": None, "side": "flat"},
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
                                "features": {"unrealized_usd": -0.2, "side": "long"},
                                "decision": {
                                    "action": "exit_position",
                                    "reasoning": "stop_loss:-0.200",
                                    "target_usd": 0.12,
                                },
                                "act": {"executed": True},
                            },
                        ],
                    }
                ],
            )
            trades, sessions = load_trades_from_decisions(p, max_sessions=5)
            self.assertEqual(len(trades), 1)
            self.assertEqual(trades[0].symbol, "SPY")
            self.assertEqual(trades[0].pattern, "pullback_uptrend")
            self.assertAlmostEqual(trades[0].exit_pnl, -0.2)
            self.assertEqual(sessions, ["2026-05-26"])

    def test_disable_pattern_blocks_trade(self):
        t = TradeRecord(
            session_date="2026-05-26",
            symbol="SPY",
            pattern="rip_fade",
            side="short",
            entry_score=-0.4,
            exit_pnl=-0.3,
            exit_reason="stop_loss",
            target_usd=0.12,
        )
        self.assertFalse(entry_would_fire(t, {"disable_patterns": ["rip_fade"]}))
        base = score_overlay([t], {"disable_patterns": []})
        blocked = score_overlay([t], {"disable_patterns": ["rip_fade"]})
        self.assertEqual(base.exits, 1)
        self.assertEqual(blocked.exits, 0)
        self.assertEqual(blocked.blocked, 1)

    def test_stress_symbol_recommends_toxic_pattern_disable(self):
        trades = [
            TradeRecord("2026-05-25", "GOOG", "rip_fade", "short", -0.3, -0.5, "stop_loss", 0.12),
            TradeRecord("2026-05-25", "GOOG", "rip_fade", "short", -0.35, -0.4, "stop_loss", 0.12),
            TradeRecord("2026-05-26", "GOOG", "pullback_uptrend", "long", 0.4, 0.15, "target_hit", 0.12),
            TradeRecord("2026-05-26", "GOOG", "pullback_uptrend", "long", 0.42, 0.12, "target_hit", 0.12),
        ]
        row = stress_symbol("GOOG", trades, holdout_session="2026-05-26")
        self.assertTrue(row.get("ok"))
        self.assertIn("rip_fade", row.get("best", {}).get("overlay", {}).get("disable_patterns", []))

    def test_apply_writes_scenario_stress_block(self):
        with tempfile.TemporaryDirectory() as td:
            import os

            os.environ["FORTRESS_AI_DATA_DIR"] = td
            learned = Path(td) / "skim_swarm" / "learned"
            learned.mkdir(parents=True)
            (learned / "SPY.json").write_text(
                json.dumps(
                    {
                        "version": 5,
                        "session_date_et": "2026-05-27",
                        "params": {"disable_patterns": [], "target_mult": 1.0},
                        "session_stats": {},
                    }
                ),
                encoding="utf-8",
            )
            report = {
                "ts": "2026-05-28T00:00:00+00:00",
                "symbols": [
                    {
                        "symbol": "SPY",
                        "ok": True,
                        "apply_recommended": True,
                        "recommended_params": {
                            "enter_long_delta": -0.02,
                            "enter_short_delta": 0.02,
                            "target_mult": 0.85,
                            "disable_patterns": ["rip_fade"],
                        },
                        "sessions": ["2026-05-26"],
                        "baseline": {},
                        "best": {},
                        "holdout_session": "2026-05-26",
                    }
                ],
            }
            applied = apply_scenario_stress_to_learned(report)
            self.assertEqual(applied, ["SPY"])
            doc = json.loads((learned / "SPY.json").read_text())
            self.assertIn("rip_fade", doc["params"]["disable_patterns"])
            self.assertEqual(doc["params"]["target_mult"], 0.85)
            self.assertIn("scenario_stress", doc)

    def test_stress_universe_empty_log(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "decisions.jsonl"
            p.write_text("", encoding="utf-8")
            report = stress_universe(max_sessions=3, decisions_path=p)
            self.assertTrue(report.get("ok"))
            self.assertEqual(report.get("trade_count"), 0)


if __name__ == "__main__":
    unittest.main()
