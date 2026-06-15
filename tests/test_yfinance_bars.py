"""yfinance intraday flatten helper."""
from __future__ import annotations

import unittest

import pandas as pd

from utils.yfinance_bars import flatten_intraday_df


class TestYfinanceBars(unittest.TestCase):
    def test_flatten_multiindex(self):
        cols = pd.MultiIndex.from_tuples(
            [("Close", "SPY"), ("High", "SPY"), ("Low", "SPY"), ("Open", "SPY"), ("Volume", "SPY")],
            names=["Price", "Ticker"],
        )
        raw = pd.DataFrame(
            [[100.0, 101.0, 99.0, 100.5, 1000]],
            columns=cols,
        )
        out = flatten_intraday_df(raw, "SPY")
        self.assertIn("Close", out.columns)
        self.assertEqual(float(out["Close"].iloc[-1]), 100.0)


if __name__ == "__main__":
    unittest.main()
