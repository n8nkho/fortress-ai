"""Skim swarm tuning: entry guard, stops, spread, per-symbol gates."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agents.skim_swarm.coordinator import EntrySlotGuard, should_halt_new_entries
from agents.skim_swarm.signal import decide, stop_loss_usd
from scripts.skim_swarm_analyze import analyze


class TestSkimTuning(unittest.TestCase):
    def setUp(self) -> None:
        self._eod = [
            patch("agents.skim_swarm.signal.is_force_flatten_window", return_value=False),
            patch("agents.skim_swarm.signal.is_eod_caution_window", return_value=False),
            patch("agents.skim_swarm.signal.is_opening_blackout", return_value=False),
            patch("agents.skim_swarm.signal.describe_eod_phase", return_value="normal"),
        ]
        for p in self._eod:
            p.start()

    def tearDown(self) -> None:
        for p in self._eod:
            p.stop()

    def test_entry_slot_guard_limits_parallel_entries(self):
        guard = EntrySlotGuard(open_count=5, max_open=6)
        self.assertTrue(guard.try_reserve())
        self.assertFalse(guard.try_reserve())
        guard.release()
        self.assertTrue(guard.try_reserve())

    def test_stop_loss_capped(self):
        # Default FORTRESS_SKIM_STOP_TARGET_MULT=0.70 — tighter stops vs targets.
        self.assertEqual(stop_loss_usd(0.10), -0.08)
        self.assertEqual(stop_loss_usd(1.0), -0.30)

    def test_trailing_giveback_only_when_profitable(self):
        features = {
            "symbol": "AMZN",
            "last": 100.0,
            "r1m": 0.0,
            "r5m": 0.0,
            "atr1m": 0.15,
            "rsi1m": 50,
            "residual_vs_spy": 0.0,
            "unrealized_usd": -0.35,
            "side": "long",
            "thin_etf": False,
        }
        st = {"side": "long", "peak_unrealized": 0.20}
        with patch("agents.skim_swarm.signal.runtime_denylist", return_value=frozenset()):
            d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertEqual(d["action"], "exit_position")
        self.assertIn("stop_loss", d["reasoning"])
        self.assertNotEqual(d["reasoning"], "trailing_giveback")

    def test_spread_blocks_entry(self):
        features = {
            "symbol": "GOOG",
            "last": 100.0,
            "r1m": -0.0005,
            "r5m": 0.002,
            "atr1m": 0.15,
            "rsi1m": 48,
            "residual_vs_spy": 0.001,
            "side": "flat",
            "thin_etf": False,
            "spread_bps": 999.0,
        }
        st = {"side": "flat", "peak_unrealized": 0}
        with patch("agents.skim_swarm.signal.runtime_denylist", return_value=frozenset()):
            d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertEqual(d["action"], "wait")
        self.assertIn("spread_too_wide", d["reasoning"])

    def test_per_symbol_spy_filter_blocks_short(self):
        features = {
            "symbol": "GOOG",
            "last": 100.0,
            "r1m": 0.0002,
            "r5m": -0.004,
            "spy_r5m": 0.001,
            "atr1m": 0.15,
            "rsi1m": 35,
            "residual_vs_spy": -0.003,
            "side": "flat",
            "thin_etf": False,
        }
        st = {"side": "flat", "peak_unrealized": 0}
        params = {
            "enter_long": 0.22,
            "enter_short": -0.22,
            "target_mult": 1.0,
            "cooldown_mult": 1.0,
            "score_bias": 0.0,
            "short_spy_filter": 0.00025,
            "pattern_deltas": {"rip_fade": 0, "pullback_uptrend": 0, "momentum_long": 0, "momentum_short": 0},
        }
        with patch("agents.skim_swarm.signal.runtime_denylist", return_value=frozenset()):
            with patch("agents.skim_swarm.signal.get_params", return_value=params):
                d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertEqual(d["action"], "wait")
        self.assertIn("symbol_short_spy_filter", d["reasoning"])

    def test_pause_long_blocks_long_entry(self):
        features = {
            "symbol": "CRWD",
            "last": 100.0,
            "r1m": -0.0005,
            "r5m": 0.003,
            "spy_r5m": 0.0,
            "atr1m": 0.15,
            "rsi1m": 52,
            "residual_vs_spy": 0.002,
            "side": "flat",
            "thin_etf": False,
        }
        st = {"side": "flat", "peak_unrealized": 0}
        params = {
            "enter_long": 0.22,
            "enter_short": -0.22,
            "target_mult": 1.0,
            "cooldown_mult": 1.0,
            "score_bias": 0.0,
            "short_spy_filter": 0.0,
            "pause_long": True,
            "pause_short": False,
            "pattern_deltas": {"rip_fade": 0, "pullback_uptrend": 0, "momentum_long": 0, "momentum_short": 0},
        }
        with patch("agents.skim_swarm.signal.runtime_denylist", return_value=frozenset()):
            with patch("agents.skim_swarm.signal.get_params", return_value=params):
                with patch("agents.skim_swarm.signal.entry_blocked_by_causation", return_value=(False, None)):
                    d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertEqual(d["action"], "wait")
        self.assertEqual(d["reasoning"], "pause_long")

    def test_pause_entries_blocks_all_entries(self):
        features = {
            "symbol": "LLY",
            "last": 100.0,
            "r1m": -0.0005,
            "r5m": 0.003,
            "spy_r5m": 0.0,
            "atr1m": 0.15,
            "rsi1m": 52,
            "residual_vs_spy": 0.002,
            "side": "flat",
            "thin_etf": False,
        }
        st = {"side": "flat", "peak_unrealized": 0}
        params = {
            "enter_long": 0.22,
            "enter_short": -0.22,
            "target_mult": 1.0,
            "cooldown_mult": 1.0,
            "score_bias": 0.0,
            "short_spy_filter": 0.0,
            "pause_long": False,
            "pause_short": False,
            "pause_entries": True,
            "pattern_deltas": {"rip_fade": 0, "pullback_uptrend": 0, "momentum_long": 0, "momentum_short": 0},
        }
        with patch("agents.skim_swarm.signal.runtime_denylist", return_value=frozenset()):
            with patch("agents.skim_swarm.signal.get_params", return_value=params):
                d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertEqual(d["action"], "wait")
        self.assertEqual(d["reasoning"], "pause_entries")

    def test_analyze_uses_window_pnl_not_cumulative(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            decisions = data_dir / "decisions.jsonl"
            now = datetime.now(timezone.utc)
            wave = {
                "ts": now.isoformat(),
                "open_positions": 1,
                "results": [
                    {
                        "symbol": "SOXX",
                        "decision": {"action": "exit_position", "reasoning": "skim_target_hit:0.2"},
                        "act": {"executed": True},
                        "features": {"unrealized_usd": 0.2},
                    }
                ],
            }
            decisions.write_text(json.dumps(wave) + "\n", encoding="utf-8")
            learned = data_dir / "learned"
            learned.mkdir()
            (learned / "SOXX.json").write_text(
                json.dumps({"symbol": "SOXX", "session_stats": {"sum_pnl_usd": -99.0, "exits": 50}}),
                encoding="utf-8",
            )
            with patch("scripts.skim_swarm_analyze._resolve_data_dir", return_value=data_dir):
                with patch("scripts.skim_swarm_analyze.session_daily_realized_usd", return_value=0.2):
                    report = analyze(minutes=30)
            self.assertAlmostEqual(report["window_realized_pnl_usd"], 0.2)

    def test_daily_stop_uses_session_pnl(self):
        with patch("agents.skim_swarm.coordinator.session_daily_realized_usd", return_value=-250.0):
            with patch("agents.skim_swarm.coordinator.daily_stop_usd", return_value=-200.0):
                halt, reason = should_halt_new_entries({"halted": False}, {})
        self.assertTrue(halt)
        self.assertIn("daily_stop", reason or "")


if __name__ == "__main__":
    unittest.main()
