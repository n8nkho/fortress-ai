"""Historical skim verification (daily proxy)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.historical_verify import SimConfig, _summarize, apply_recommendations_to_learned


class TestHistoricalVerify(unittest.TestCase):
    def test_summarize_empty(self):
        s = _summarize([])
        self.assertEqual(s["trades"], 0)

    def test_sim_config_defaults(self):
        c = SimConfig()
        self.assertEqual(c.years, 10)

    def test_apply_copies_disable_patterns(self):
        report = {
            "ts": "2026-05-22T00:00:00+00:00",
            "symbols": [
                {
                    "ok": True,
                    "symbol": "SPY",
                    "history_start": "2016-01-01",
                    "history_end": "2026-05-22",
                    "recommended_params": {
                        "enter_long_delta": -0.02,
                        "disable_patterns": ["momentum_short", "rip_fade"],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning._learned_dir", return_value=Path(td)):
                applied = apply_recommendations_to_learned(report)
            self.assertEqual(applied, ["SPY"])
            learned = (Path(td) / "SPY.json").read_text(encoding="utf-8")
            self.assertIn("momentum_short", learned)
            self.assertIn("rip_fade", learned)

    def test_apply_sets_historical_seed_disables(self):
        report = {
            "ts": "2026-05-22T00:00:00+00:00",
            "symbols": [
                {
                    "ok": True,
                    "symbol": "LLY",
                    "history_start": "2016-01-01",
                    "history_end": "2026-05-22",
                    "recommended_params": {
                        "disable_patterns": ["rip_fade", "pullback_uptrend", "momentum_short"],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            with patch("agents.skim_swarm.symbol_learning._learned_dir", return_value=Path(td)):
                apply_recommendations_to_learned(report)
                from agents.skim_swarm.symbol_learning import load_learned, refresh_historical_seeds, save_learned

                L = load_learned("LLY")
                L["params"]["disable_patterns"] = ["momentum_long"]
                save_learned("LLY", L)
                self.assertEqual(refresh_historical_seeds(L), True)
                self.assertEqual(
                    L["params"]["disable_patterns"],
                    ["rip_fade", "pullback_uptrend", "momentum_short"],
                )
                self.assertNotIn("momentum_long", L["params"]["disable_patterns"])


if __name__ == "__main__":
    unittest.main()
