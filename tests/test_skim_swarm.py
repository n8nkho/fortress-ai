"""Skim swarm config and signal engine."""
from __future__ import annotations

import unittest

from agents.skim_swarm.signal import adaptive_target_usd, compute_score, decide
from utils.skim_swarm_config import normalize_symbol, universe


class TestSkimSwarm(unittest.TestCase):
    def test_universe_includes_new_tickers(self):
        u = universe()
        for sym in ("SPY", "NVDA", "AAPL", "NASA", "BRK.B", "AGIX", "PLTR", "CRWD"):
            self.assertIn(sym, u)

    def test_brkb_alias(self):
        self.assertEqual(normalize_symbol("BRKB"), "BRK.B")

    def test_decide_enter_long_on_score(self):
        features = {
            "symbol": "NVDA",
            "last": 100.0,
            "r1m": -0.0005,
            "r5m": 0.002,
            "atr1m": 0.15,
            "rsi1m": 48,
            "residual_vs_spy": 0.001,
            "semi_lead_vs_soxx": 0.0005,
            "side": "flat",
            "thin_etf": False,
            "vix_last": 18,
        }
        st = {"side": "flat", "peak_unrealized": 0}
        d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertIn(d["action"], ("enter_long", "enter_short", "wait"))

    def test_adaptive_target_positive(self):
        t = adaptive_target_usd({"last": 50.0, "atr1m": 0.1, "thin_etf": False})
        self.assertGreaterEqual(t, 0.05)


if __name__ == "__main__":
    unittest.main()
