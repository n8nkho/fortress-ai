"""Alpaca bar provider for skim swarm."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from agents.skim_swarm.alpaca_bars import _normalize_symbol_df, fetch_intraday_bars
from agents.skim_swarm.features import _fetch_bars


class TestSkimAlpacaBars(unittest.TestCase):
    def setUp(self) -> None:
        from agents.skim_swarm import features

        features._bar_cache.clear()

    def test_normalize_symbol_df(self):
        idx = pd.to_datetime(["2026-05-22 14:30:00", "2026-05-22 14:31:00"], utc=True)
        raw = pd.DataFrame(
            {
                "open": [100.0, 100.5],
                "high": [100.2, 100.8],
                "low": [99.8, 100.3],
                "close": [100.1, 100.6],
                "volume": [1000, 1100],
            },
            index=idx,
        )
        out = _normalize_symbol_df(raw, "NVDA")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("Close", out.columns)
        self.assertAlmostEqual(float(out["Close"].iloc[-1]), 100.6)

    def test_fetch_bars_prefers_alpaca(self):
        idx = pd.to_datetime(["2026-05-22 14:30:00", "2026-05-22 14:31:00"], utc=True)
        alpaca_df = pd.DataFrame({"Close": [500.0, 501.0]}, index=idx)
        with patch("agents.skim_swarm.features._use_alpaca_bars", return_value=True):
            with patch(
                "agents.skim_swarm.alpaca_bars.fetch_intraday_bars",
                return_value={"NVDA": alpaca_df},
            ):
                with patch("agents.skim_swarm.features.yf.download") as yf_dl:
                    out = _fetch_bars(["NVDA"])
        self.assertIn("NVDA", out)
        self.assertAlmostEqual(float(out["NVDA"]["Close"].iloc[-1]), 501.0)
        yf_dl.assert_not_called()

    def test_fetch_bars_falls_back_to_yfinance(self):
        yf_idx = pd.to_datetime(["2026-05-22 14:30:00"], utc=True)
        yf_df = pd.DataFrame({"Close": [400.0]}, index=yf_idx)
        with patch("agents.skim_swarm.features._use_alpaca_bars", return_value=True):
            with patch("agents.skim_swarm.alpaca_bars.fetch_intraday_bars", return_value={}):
                with patch("agents.skim_swarm.features.yf.download", return_value=yf_df):
                    out = _fetch_bars(["MSFT"])
        self.assertIn("MSFT", out)
        self.assertAlmostEqual(float(out["MSFT"]["Close"].iloc[-1]), 400.0)

    def test_fetch_intraday_bars_batches(self):
        idx = pd.MultiIndex.from_product(
            [["NVDA"], pd.to_datetime(["2026-05-22 14:30:00"], utc=True)],
            names=["symbol", "timestamp"],
        )
        raw = pd.DataFrame(
            {"open": [100.0], "high": [100.2], "low": [99.8], "close": [100.1], "volume": [1000]},
            index=idx,
        )
        mock_resp = MagicMock()
        mock_resp.df = raw
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = mock_resp
        with patch("agents.skim_swarm.alpaca_bars._data_client", return_value=mock_client):
            with patch("agents.skim_swarm.alpaca_bars.alpaca_credentials", return_value=("k", "s")):
                out = fetch_intraday_bars(["NVDA"], feed="iex")
        self.assertIn("NVDA", out)
        self.assertEqual(mock_client.get_stock_bars.call_count, 1)


if __name__ == "__main__":
    unittest.main()
