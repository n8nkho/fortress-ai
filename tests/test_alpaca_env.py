"""Alpaca env helpers (credentials + TradingClient kwargs)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestAlpacaEnv(unittest.TestCase):
    def test_strip_quotes_from_credentials(self):
        from utils import alpaca_env

        os.environ["ALPACA_API_KEY"] = '"PKABC123"'
        os.environ["ALPACA_SECRET_KEY"] = "'secXYZ'"
        try:
            k, s = alpaca_env.alpaca_credentials()
            self.assertEqual(k, "PKABC123")
            self.assertEqual(s, "secXYZ")
        finally:
            del os.environ["ALPACA_API_KEY"]
            del os.environ["ALPACA_SECRET_KEY"]

    def test_trading_client_kwargs_url_override_when_base_set(self):
        from utils import alpaca_env

        os.environ["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"
        try:
            kw = alpaca_env.alpaca_trading_client_kwargs()
            self.assertTrue(kw.get("paper"))
            self.assertEqual(
                kw.get("url_override"), "https://paper-api.alpaca.markets"
            )
        finally:
            del os.environ["ALPACA_BASE_URL"]

    def test_trading_client_kwargs_no_override_when_base_empty(self):
        from utils import alpaca_env

        old = os.environ.pop("ALPACA_BASE_URL", None)
        try:
            kw = alpaca_env.alpaca_trading_client_kwargs()
            self.assertNotIn("url_override", kw)
            self.assertTrue(kw.get("paper"))
        finally:
            if old is not None:
                os.environ["ALPACA_BASE_URL"] = old


if __name__ == "__main__":
    unittest.main()
