"""Tests for swarm halt semantics, wave SI, and integrity extensions."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.infra_swarm.signal import decide as infra_decide
from agents.skim_swarm.signal import decide as skim_decide


class TestSwarmHaltExitSemantics(unittest.TestCase):
    def test_skim_halted_still_exits_at_target(self):
        features = {
            "symbol": "COHR",
            "last": 100.0,
            "r1m": 0.0,
            "r5m": 0.0,
            "side": "long",
            "unrealized_usd": 0.15,
            "thin_etf": False,
        }
        st = {"side": "long", "peak_unrealized": 0.15}
        params = {
            "enter_long": 0.2,
            "enter_short": -0.2,
            "target_mult": 1.0,
            "target_mult_effective": 1.0,
            "stop_target_mult_effective": 0.7,
            "spread_bps_mult": 1.0,
            "pause_entries": False,
            "pattern_deltas": {},
            "disable_patterns": [],
            "score_bias": 0,
            "cooldown_mult": 1,
        }
        with patch("agents.skim_swarm.signal.get_params", return_value=params):
            with patch("agents.skim_swarm.signal.is_force_flatten_window", return_value=False):
                with patch("agents.skim_swarm.signal.is_eod_caution_window", return_value=False):
                    with patch("agents.skim_swarm.signal.describe_eod_phase", return_value="normal"):
                        d = skim_decide(features, st, swarm_halted=True, open_positions=1, max_open=6)
        self.assertEqual(d["action"], "exit_position")
        self.assertIn("skim_target_hit", d["reasoning"])

    def test_infra_halted_still_exits_at_target(self):
        features = {
            "symbol": "MRVL",
            "layer": "L1",
            "last": 200.0,
            "r1m": 0.0,
            "r5m": 0.0,
            "side": "long",
            "unrealized_usd": 0.12,
            "spread_bps": 5,
        }
        st = {"side": "long", "peak_unrealized": 0.12}
        params = {
            "enter_long": 0.2,
            "enter_short": -0.2,
            "target_mult": 1.0,
            "target_mult_effective": 1.0,
            "stop_target_mult_effective": 0.7,
            "spread_bps_mult": 1.0,
            "pause_entries": False,
            "pause_long": False,
            "pause_short": False,
            "pattern_deltas": {},
            "disable_patterns": [],
            "score_bias": 0,
            "cooldown_mult": 1,
        }
        with patch("agents.infra_swarm.signal.get_params", return_value=params):
            with patch("agents.infra_swarm.signal.is_force_flatten_window", return_value=False):
                with patch("agents.infra_swarm.signal.is_eod_caution_window", return_value=False):
                    with patch("agents.infra_swarm.signal.is_opening_blackout", return_value=False):
                        with patch("agents.infra_swarm.signal.describe_eod_phase", return_value="normal"):
                            d = infra_decide(features, st, swarm_halted=True, open_positions=1, max_open=6)
        self.assertEqual(d["action"], "exit_position")
        self.assertIn("infra_target_hit", d["reasoning"])

    def test_skim_halted_blocks_new_entries(self):
        features = {
            "symbol": "AAPL",
            "last": 100.0,
            "r1m": 0.002,
            "r5m": 0.003,
            "side": "flat",
            "thin_etf": False,
            "spread_bps": 5,
        }
        st = {"side": "flat"}
        params = {
            "enter_long": 0.05,
            "enter_short": -0.05,
            "target_mult": 1.0,
            "target_mult_effective": 1.0,
            "stop_target_mult_effective": 0.7,
            "spread_bps_mult": 1.0,
            "pause_entries": False,
            "pattern_deltas": {},
            "disable_patterns": [],
            "score_bias": 0,
            "cooldown_mult": 1,
        }
        with patch("agents.skim_swarm.signal.get_params", return_value=params):
            with patch("agents.skim_swarm.signal.is_force_flatten_window", return_value=False):
                with patch("agents.skim_swarm.signal.is_eod_caution_window", return_value=False):
                    with patch("agents.skim_swarm.signal.is_opening_blackout", return_value=False):
                        with patch("agents.skim_swarm.signal.describe_eod_phase", return_value="normal"):
                            d = skim_decide(features, st, swarm_halted=True, open_positions=0, max_open=6)
        self.assertEqual(d["reasoning"], "swarm_halted")


class TestSwarmRuntime(unittest.TestCase):
    def test_wave_symbols_unions_open_positions(self):
        from utils.swarm_runtime import wave_symbols

        syms = wave_symbols(["SPY", "MSFT"], {"NVDA": {"side": "long", "qty": 1}})
        self.assertIn("NVDA", syms)
        self.assertIn("SPY", syms)

    def test_refresh_universe_if_changed(self):
        from utils.swarm_runtime import refresh_universe_if_changed

        fresh, event = refresh_universe_if_changed(["A", "B"], lambda: ["A", "C"])
        self.assertEqual(fresh, ["A", "C"])
        self.assertIsNotNone(event)
        self.assertIn("B", event["removed"])


class TestIntegrityHaltScan(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td.name

    def test_detects_halt_blocked_exit_in_journal(self):
        p = Path(self._td.name) / "infra_swarm" / "decisions.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        wave = {
            "swarm_halted": True,
            "open_positions": 1,
            "results": [
                {
                    "symbol": "COHR",
                    "features": {"side": "long", "unrealized_usd": 1.2},
                    "decision": {"action": "wait", "reasoning": "swarm_halted", "target_usd": 0.1},
                }
            ],
        }
        p.write_text(json.dumps(wave) + "\n", encoding="utf-8")

        from utils.integrity_diagnostics import scan_infra_swarm

        findings = scan_infra_swarm()
        codes = [f["code"] for f in findings]
        self.assertIn("halt_blocked_exit", codes)


if __name__ == "__main__":
    unittest.main()
