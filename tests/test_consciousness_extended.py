"""Extended consciousness — intent, analogues, events, counterfactual."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from utils.analogue_days import find_analogue_days
from utils.market_event_calendar import events_for_date
from utils.session_intent import generate_session_intent


class TestAnalogueDays(unittest.TestCase):
    def test_find_analogues_from_csv(self):
        with tempfile.TemporaryDirectory() as td:
            prices = Path(td) / "prices"
            prices.mkdir()
            dates = pd.date_range("2024-01-02", periods=120, freq="B")
            close = [100 + i * 0.1 for i in range(len(dates))]
            spy = pd.DataFrame({"date": dates, "close": close, "open": close, "high": close, "low": close, "volume": 1})
            spy.to_csv(prices / "SPY_daily.csv", index=False)
            vix = pd.DataFrame({"date": dates, "close": [18.0] * len(dates)})
            vix.to_csv(prices / "VIX_daily.csv", index=False)
            with patch("utils.analogue_days._read_daily") as mock_read:
                def _side(sym: str):
                    p = prices / f"{sym}_daily.csv"
                    df = pd.read_csv(p, parse_dates=["date"])
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    return df.dropna(subset=["date", "close"]).sort_values("date")

                mock_read.side_effect = _side
                out = find_analogue_days(k=3, live_tape={"change_1d_pct": 0.2, "change_5d_pct": 0.5, "vix_last": 18})
            self.assertGreaterEqual(len(out), 1)


class TestSessionIntent(unittest.TestCase):
    def test_generate_intent(self):
        mc = {
            "temporal": {"slot_key": "Mon-10", "hour_et": 10, "weekday": "Mon"},
            "market_tape": {"tape_trend": "uptrend", "change_1d_pct": 0.4, "strong_tape_1d": True},
            "historical_hour_profile": {"SPY": {"mean_return_pct": 0.03, "win_rate_long": 0.56}},
            "self_state": {"alpha_vs_spy_pct": -0.4},
            "analogue_days": [],
        }
        with patch("utils.market_event_calendar.event_summary", return_value={"events": [], "has_high_impact": False}):
            doc = generate_session_intent(consciousness=mc)
        self.assertIn("participation_target", doc)
        self.assertIn("plan_line", doc)


class TestEventCalendar(unittest.TestCase):
    def test_fomc_date(self):
        ev = events_for_date(datetime(2026, 6, 17, tzinfo=ZoneInfo("America/New_York")).date())
        self.assertTrue(any(e.get("type") == "fomc" for e in ev))


if __name__ == "__main__":
    unittest.main()
