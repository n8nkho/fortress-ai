"""Market consciousness — hourly memory inputs."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from agents.historical_seeder.hourly_knowledge import build_hourly_knowledge, build_symbol_slot_stats
from utils.market_consciousness import (
    assemble_consciousness_inputs,
    current_temporal_slot,
    format_consciousness_prompt_section,
    slot_profile,
)


class TestHourlyKnowledge(unittest.TestCase):
    def test_build_symbol_slot_stats(self):
        # Same Tue 14:00 ET slot across weeks (min 8 samples per slot)
        base = datetime(2024, 1, 2, 14, 0, tzinfo=ZoneInfo("America/New_York"))
        dates = [base + timedelta(weeks=i) for i in range(12)]
        ts = pd.to_datetime(dates, utc=True)
        close = [100 + i * 0.1 for i in range(12)]
        df = pd.DataFrame({"ts": ts, "close": close})
        stats = build_symbol_slot_stats(df)
        self.assertIn("Tue-14", stats)
        self.assertGreaterEqual(stats["Tue-14"]["sample_count"], 8)

    def test_build_knowledge_from_csv(self):
        with tempfile.TemporaryDirectory() as td:
            hourly = Path(td) / "hourly"
            hourly.mkdir()
            base = datetime(2024, 1, 2, 14, 0, tzinfo=ZoneInfo("America/New_York"))
            dates = [base + timedelta(weeks=i) for i in range(12)]
            ts = pd.to_datetime(dates, utc=True)
            close = [100 + (i % 5) * 0.2 for i in range(12)]
            df = pd.DataFrame(
                {
                    "ts": ts,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000,
                }
            )
            df.to_csv(hourly / "SPY_hourly.csv", index=False)
            with patch("agents.historical_seeder.hourly_knowledge.hourly_dir", return_value=hourly):
                doc = build_hourly_knowledge(symbols=["SPY"])
            self.assertIn("SPY", doc.get("symbols") or [])
            self.assertIn("SPY", doc.get("slots") or {})


class TestMarketConsciousness(unittest.TestCase):
    def test_current_temporal_slot_rth(self):
        t = datetime(2026, 6, 12, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        slot = current_temporal_slot(now=t)
        self.assertTrue(slot["rth_active"])
        self.assertEqual(slot["slot_key"], "Fri-14")

    def test_consciousness_with_knowledge(self):
        kb = {
            "version": 1,
            "built_at": "2026-06-12",
            "symbols": ["SPY"],
            "slots": {
                "SPY": {
                    "Fri-14": {
                        "mean_return_pct": 0.05,
                        "win_rate_long": 0.55,
                        "sample_count": 200,
                    }
                }
            },
        }
        t = datetime(2026, 6, 12, 14, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch("utils.market_consciousness.load_knowledge_base", return_value=kb):
            with patch(
                "utils.market_benchmark.fetch_benchmark_context",
                return_value={"ok": True, "benchmark": "SPY", "change_1d_pct": 0.5, "tape_trend": "mixed"},
            ):
                with patch(
                    "utils.market_benchmark.build_portfolio_session_metrics",
                    return_value={"session_realized_usd": -0.3, "alpha_vs_spy_pct": -0.5},
                ):
                    with patch("utils.operator_halt.is_trading_halted", return_value=False):
                        bundle = assemble_consciousness_inputs(now=t)
        self.assertIn("SPY", bundle.get("historical_hour_profile") or {})
        self.assertTrue(bundle.get("analogue_summary"))
        with patch("utils.market_consciousness.assemble_consciousness_inputs", return_value=bundle):
            prompt = format_consciousness_prompt_section()
        self.assertIn("MARKET_CONSCIOUSNESS", prompt)

    def test_slot_profile_miss(self):
        self.assertIsNone(slot_profile({"slots": {}}, "SPY", "Mon-09"))


if __name__ == "__main__":
    unittest.main()
