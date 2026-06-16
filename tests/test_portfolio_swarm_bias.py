"""Tests for portfolio swarm bias."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestPortfolioSwarmBias(unittest.TestCase):
    def setUp(self):
        os.environ["FORTRESS_PORTFOLIO_SWARM_BIAS"] = "1"
        os.environ["FORTRESS_SKIM_WAVE_REDUCE_RATIO"] = "0.5"

    def test_reduces_flat_symbols_when_active(self):
        from utils.portfolio_swarm_bias import filter_skim_wave_symbols

        with patch("utils.portfolio_swarm_bias.skim_wave_reduce_active", return_value=True):
            syms, meta = filter_skim_wave_symbols(
                ["AAPL", "MSFT", "GOOG", "AMZN", "SPY"],
                owned_symbols={"SPY"},
            )
        self.assertLess(len(syms), 5)
        self.assertIn("SPY", syms)
        self.assertTrue(meta and meta.get("skim_wave_reduced"))

    def test_no_change_when_inactive(self):
        from utils.portfolio_swarm_bias import filter_skim_wave_symbols

        with patch("utils.portfolio_swarm_bias.skim_wave_reduce_active", return_value=False):
            syms, meta = filter_skim_wave_symbols(["AAPL", "MSFT"], owned_symbols=set())
        self.assertEqual(syms, ["AAPL", "MSFT"])
        self.assertIsNone(meta)


if __name__ == "__main__":
    unittest.main()
