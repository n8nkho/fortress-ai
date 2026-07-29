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
from config.constants import KNOWLEDGE_BASE_MAX_AGE_DAYS
from utils.market_consciousness import (
    assemble_consciousness_inputs,
    current_temporal_slot,
    format_consciousness_prompt_section,
    rebuild_hourly_knowledge,
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
        stats, _reg = build_symbol_slot_stats(df)
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
                        bundle = assemble_consciousness_inputs(now=t, use_cache=False)
        self.assertIn("SPY", bundle.get("historical_hour_profile") or {})
        self.assertTrue(bundle.get("analogue_summary"))
        with patch("utils.market_consciousness.assemble_consciousness_inputs", return_value=bundle):
            prompt = format_consciousness_prompt_section()
        self.assertIn("MARKET_CONSCIOUSNESS", prompt)

    def test_slot_profile_miss(self):
        self.assertIsNone(slot_profile({"slots": {}}, "SPY", "Mon-09"))

    def test_assemble_no_recursion_with_session_intent(self):
        with patch("utils.session_intent.load_session_intent", return_value={"plan_line": "test"}):
            bundle = assemble_consciousness_inputs(use_cache=False)
        self.assertTrue(bundle.get("enabled"))
        self.assertNotIn("recursive_guard", bundle)


class TestConsciousnessKnowledgeBaseRebuild(unittest.TestCase):
    def test_rebuild_hourly_knowledge_skips_when_fresh(self):
        built = datetime(2026, 6, 20, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        with patch(
            "utils.market_consciousness_knowledge_base.knowledge_base_last_build_time",
            return_value=built,
        ):
            with patch(
                "utils.market_consciousness_knowledge_base.now",
                return_value=built + timedelta(days=1),
            ):
                out = rebuild_hourly_knowledge(force=False)
        self.assertTrue(out.get("skipped"))
        self.assertEqual(out.get("block_reason"), "consciousness_kb_fresh")

    def test_rebuild_hourly_knowledge_rebuilds_when_stale(self):
        built = datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        fake_out = {"symbols": ["SPY"], "knowledge_path": "data/hourly/market_hourly_knowledge.json"}
        with patch(
            "utils.market_consciousness_knowledge_base.knowledge_base_last_build_time",
            return_value=built,
        ):
            with patch(
                "utils.market_consciousness_knowledge_base.now",
                return_value=built + timedelta(days=KNOWLEDGE_BASE_MAX_AGE_DAYS + 1),
            ):
                with patch(
                    "agents.historical_seeder.hourly_knowledge.run_build",
                    return_value=fake_out,
                ) as run_build:
                    out = rebuild_hourly_knowledge(force=False, download=False)
        run_build.assert_called_once_with(download=False, force_download=False)
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out.get("block_reason"), "consciousness_kb_rebuilt")

    def test_si_processor_registers_rebuild_hourly_knowledge(self):
        from utils.si_queue.handlers import handle_rebuild_hourly_knowledge
        from utils.si_queue.si_processor import ACTION_HANDLERS, process_si_action

        self.assertIs(ACTION_HANDLERS.get("rebuild_hourly_knowledge"), handle_rebuild_hourly_knowledge)
        expected = {"skipped": True, "block_reason": "consciousness_kb_fresh"}
        with patch(
            "utils.market_consciousness_knowledge_base.rebuild_hourly_knowledge",
            return_value=expected,
        ):
            out = process_si_action("rebuild_hourly_knowledge")
        self.assertEqual(out.get("si_action"), "rebuild_hourly_knowledge")
        self.assertEqual(out.get("block_reason"), "consciousness_kb_fresh")


if __name__ == "__main__":
    unittest.main()
