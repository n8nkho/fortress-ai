"""Skim universe quotes."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from tests.optional_deps import has_yfinance

if not has_yfinance():
    @unittest.skip("yfinance not installed (pip install -r requirements.txt)")
    class TestSkimQuotes(unittest.TestCase):
        pass
else:
    from agents.skim_swarm.quotes import build_symbol_quotes

    class TestSkimQuotes(unittest.TestCase):
        def test_build_symbol_quotes_open_marker(self):
            bars = {
                "NVDA": pd.DataFrame({"Close": [100.0, 101.5]}),
                "MSFT": pd.DataFrame({"Close": [400.0, 399.0]}),
            }
            positions = {
                "NVDA": {
                    "symbol": "NVDA",
                    "qty": 1,
                    "side": "long",
                    "avg_entry_price": 100.0,
                    "current_price": 101.5,
                    "unrealized_pl": 1.5,
                    "unrealized_plpc": 0.015,
                },
                "MSFT": {"symbol": "MSFT", "qty": 0, "side": "flat", "avg_entry_price": None},
            }
            with patch("agents.skim_swarm.quotes.fetch_positions_map", return_value=(100000.0, 50000.0, positions)):
                with patch("agents.skim_swarm.quotes._fetch_bars", return_value=bars):
                    out = build_symbol_quotes(["NVDA", "MSFT"])
            self.assertTrue(out["NVDA"]["is_open"])
            self.assertFalse(out["MSFT"]["is_open"])
            self.assertEqual(out["NVDA"]["last"], 101.5)
            self.assertAlmostEqual(out["NVDA"]["change_pct"], 1.5, places=2)


if __name__ == "__main__":
    unittest.main()
