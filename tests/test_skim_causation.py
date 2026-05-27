"""Per-symbol causation tracking."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.symbol_causation import (
    build_causation_key,
    build_entry_context,
    causation_blocks_entry,
    ensure_causation,
    record_causation_exit,
)
from agents.skim_swarm.symbol_learning import entry_blocked_by_causation, load_learned, record_decision


class TestSkimCausation(unittest.TestCase):
    def test_causation_key_buckets(self):
        features = {"spy_r5m": 0.001, "r5m": -0.002, "score": -0.42}
        key = build_causation_key(pattern="rip_fade", side="short", features=features, score=-0.42)
        self.assertEqual(key, "rip_fade|short|spy_pos|sym_neg|score_strong")

    def test_eliminates_losing_context_after_samples(self):
        learned = {"causation": ensure_causation({})}
        ctx = build_entry_context(
            pattern="rip_fade",
            side="short",
            features={"spy_r5m": 0.001, "r5m": -0.002, "score": -0.4},
            score=-0.4,
            target_usd=0.12,
        )
        for pnl in (-0.18, -0.17):
            record_causation_exit(
                learned,
                entry_context=ctx,
                exit_reasoning="stop_loss:-0.300",
                pnl_usd=pnl,
            )
        key = ctx["causation_key"]
        self.assertIn(key, learned["causation"]["eliminated_keys"])
        blocked, reason = causation_blocks_entry(
            "NVDA",
            learned,
            pattern="rip_fade",
            side="short",
            features={"spy_r5m": 0.001, "r5m": -0.002, "score": -0.4},
            score=-0.4,
        )
        self.assertTrue(blocked)
        self.assertIn("causation_eliminated", reason or "")

    def test_causation_persists_across_session_reset(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    for _ in range(2):
                        record_decision(
                            "CRWD",
                            decision={"action": "enter_short", "reasoning": "rip_fade score=-0.40", "score": -0.4},
                            act_result={"executed": True},
                            features={"r5m": -0.002, "spy_r5m": 0.001, "side": "flat"},
                        )
                        record_decision(
                            "CRWD",
                            decision={"action": "exit_position", "reasoning": "stop_loss:-0.300"},
                            act_result={"executed": True},
                            features={"unrealized_usd": -0.3, "side": "short"},
                        )
                    L = load_learned("CRWD")
                    self.assertGreater(len(L["causation"]["eliminated_keys"]), 0)
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-23"):
                    L2 = load_learned("CRWD")
                    self.assertEqual(int(L2["session_stats"]["exits"]), 0)
                    self.assertGreater(len(L2["causation"]["eliminated_keys"]), 0)
                    self.assertGreater(len(L2["causation"]["keys"]), 0)

    def test_entry_blocked_by_causation_integration(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning.swarm_data_dir", return_value=Path(td)):
                with patch("agents.skim_swarm.symbol_learning.session_date_et", return_value="2026-05-22"):
                    L = load_learned("SOXX")
                    key = "pullback_uptrend|long|spy_flat|sym_pos|score_med"
                    ensure_causation(L)
                    L["causation"]["eliminated_keys"] = [key]
                    from agents.skim_swarm.symbol_learning import save_learned

                    save_learned("SOXX", L)
                    blocked, _ = entry_blocked_by_causation(
                        "SOXX",
                        pattern="pullback_uptrend",
                        side="long",
                        features={"spy_r5m": 0.0, "r5m": 0.001, "score": 0.25},
                        score=0.25,
                    )
                    self.assertTrue(blocked)


if __name__ == "__main__":
    unittest.main()
