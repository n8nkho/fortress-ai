"""AI infra swarm — adaptive universe, layer features, SRP signal."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.optional_deps import has_yfinance

if not has_yfinance():
    @unittest.skip("yfinance not installed (pip install -r requirements.txt)")
    class TestInfraSwarm(unittest.TestCase):
        pass
else:
    from agents.infra_swarm.adaptive_universe import refresh_adaptive_universe
    from agents.infra_swarm.features import build_symbol_features
    from agents.infra_swarm.signal import decide, stop_loss_usd
    from utils.infra_swarm_config import layer_for_symbol, read_active_universe, universe

    class TestInfraSwarm(unittest.TestCase):
        def test_layer_mapping(self):
            self.assertEqual(layer_for_symbol("NVDA"), "L1")
            self.assertEqual(layer_for_symbol("SMCI"), "L2")
            self.assertEqual(layer_for_symbol("AMAT"), "L3")
            self.assertEqual(layer_for_symbol("VRT"), "L4")

        def test_adaptive_universe_refresh(self):
            with tempfile.TemporaryDirectory() as td:
                with patch("agents.infra_swarm.adaptive_universe.swarm_data_dir", return_value=Path(td)):
                    with patch("utils.infra_swarm_config._swarm_data_dir_path", return_value=Path(td)):
                        out = refresh_adaptive_universe(force=True)
                        self.assertIn("active", out)
                        self.assertGreaterEqual(len(out["active"]), 6)
                        active = read_active_universe()
                        self.assertEqual(len(active), len(out["active"]))

        def test_build_symbol_features_layer_residual(self):
            shared = {
                "l1_r5m": 0.002,
                "l2_r5m": 0.001,
                "anchor_r5m": 0.0015,
                "stack_stress": 2,
                "stack_direction": 1,
                "infra_breadth": 0.6,
                "symbols": {"SMCI": {"r5m": 0.0005, "last": 100.0}},
            }
            bars = {"SMCI": __import__("pandas").DataFrame({
                "Close": [98, 98.5, 99, 99.2, 99.5, 99.8, 100, 100],
                "High": [98.5, 99, 99.5, 99.7, 100, 100.2, 100.2, 100.2],
                "Low": [97.5, 98, 98.5, 99, 99.2, 99.5, 99.8, 99.8],
            })}
            feat = build_symbol_features("SMCI", bars, shared, position={"side": "flat", "qty": 0})
            self.assertEqual(feat["layer"], "L2")
            self.assertIsNotNone(feat.get("residual_vs_layer"))
            self.assertIsNotNone(feat.get("propagation_lag_vs_l1"))

        def test_stop_loss_capped(self):
            self.assertEqual(stop_loss_usd(0.10), -0.08)

        def test_decide_respects_pause_entries(self):
            features = {
                "symbol": "NVDA",
                "layer": "L1",
                "last": 100.0,
                "r1m": 0.0,
                "r5m": 0.0,
                "side": "flat",
                "spread_bps": 5,
            }
            st = {"side": "flat"}
            params = {
                "enter_long": 0.2,
                "enter_short": -0.2,
                "target_mult": 1.0,
                "target_mult_effective": 1.0,
                "stop_target_mult_effective": 0.7,
                "spread_bps_mult": 1.0,
                "pause_entries": True,
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
                                d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
            self.assertEqual(d.get("reasoning"), "pause_entries")

        def test_universe_not_empty(self):
            syms = universe()
            self.assertGreaterEqual(len(syms), 6)
            self.assertIn("NVDA", syms)


if __name__ == "__main__":
    unittest.main()
