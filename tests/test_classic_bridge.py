"""Classic Fortress bridge helpers (screener + symbol extraction)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestClassicBridge(unittest.TestCase):
    def test_symbols_from_ai_decision_rows_includes_screen_market(self):
        from utils.classic_bridge import symbols_from_ai_decision_rows

        rows = [
            {
                "decision": {
                    "action": "screen_market",
                    "parameters": {"watchlist": ["aapl", "MSFT"]},
                }
            },
            {"decision": {"action": "enter_position", "parameters": {"symbol": "xlv"}}},
        ]
        syms = symbols_from_ai_decision_rows(rows)
        self.assertEqual(syms, {"AAPL", "MSFT", "XLV"})

    def test_classic_screener_candidates_daily_signals(self):
        from utils import classic_bridge as cb

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            sig = data / "daily_signals_20990101.json"
            sig.write_text(
                json.dumps(
                    {
                        "timestamp": "2099-01-01T12:00:00+00:00",
                        "candidates": [{"ticker": "NVDA"}, {"ticker": "amd"}],
                    }
                ),
                encoding="utf-8",
            )
            old = cb.resolve_classic_data_dir
            cb.resolve_classic_data_dir = lambda: data  # type: ignore[method-assign]
            try:
                out = cb.classic_screener_candidates(max_symbols=5)
            finally:
                cb.resolve_classic_data_dir = old  # type: ignore[method-assign]
            self.assertEqual(out["source"], "classic_daily_signals")
            self.assertEqual(out["symbols"], ["NVDA", "AMD"])

    def test_screener_hint_falls_back_to_classic(self):
        import dashboard.ai_command_center as acc

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            sig = data / "daily_signals_20990102.json"
            sig.write_text(
                json.dumps({"candidates": [{"ticker": "SPY"}]}),
                encoding="utf-8",
            )
            old = acc.resolve_classic_data_dir if hasattr(acc, "resolve_classic_data_dir") else None
            from utils import classic_bridge as cb

            prev = cb.resolve_classic_data_dir
            cb.resolve_classic_data_dir = lambda: data  # type: ignore[method-assign]
            try:
                hint = acc._screener_hint([])
            finally:
                cb.resolve_classic_data_dir = prev  # type: ignore[method-assign]
            self.assertEqual(hint["symbols"], ["SPY"])
            self.assertEqual(hint["source"], "classic_daily_signals")


if __name__ == "__main__":
    unittest.main()
