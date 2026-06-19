"""Tests for swarm buying-power short entry gate."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from utils.swarm_buying_power import min_buying_power_for_short_usd, short_entry_blocked


class TestSwarmBuyingPower(unittest.TestCase):
    def test_blocks_short_when_buying_power_low(self):
        with mock.patch.dict(os.environ, {"FORTRESS_SWARM_SHORT_MIN_BUYING_POWER_USD": "150"}):
            blocked, reason = short_entry_blocked(
                {"buying_power_usd": 32.0, "last": 200.0},
                action="enter_short",
            )
        self.assertTrue(blocked)
        self.assertIn("insufficient_buying_power_short", reason)

    def test_allows_short_when_buying_power_sufficient(self):
        blocked, _ = short_entry_blocked(
            {"buying_power_usd": 5000.0, "last": 100.0},
            action="enter_short",
        )
        self.assertFalse(blocked)

    def test_skips_when_buying_power_missing(self):
        blocked, _ = short_entry_blocked({}, action="enter_short")
        self.assertFalse(blocked)

    def test_respects_disable_env(self):
        with mock.patch.dict(os.environ, {"FORTRESS_SWARM_SHORT_BP_GATE": "0"}):
            blocked, _ = short_entry_blocked({"buying_power_usd": 1.0}, action="enter_short")
        self.assertFalse(blocked)

    def test_signal_blocks_short_entry_in_decide(self):
        from agents.skim_swarm.signal import decide

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
            "buying_power_usd": 30.0,
        }
        st = {"side": "flat", "peak_unrealized": 0}
        params = {
            "enter_long": 0.22,
            "enter_short": -0.22,
            "target_mult": 1.0,
            "cooldown_mult": 1.0,
            "score_bias": 0.0,
            "short_spy_filter": 0.0,
            "pattern_deltas": {"rip_fade": 0, "pullback_uptrend": 0, "momentum_long": 0, "momentum_short": 0},
        }
        with mock.patch("agents.skim_swarm.signal.runtime_denylist", return_value=frozenset()):
            with mock.patch("agents.skim_swarm.signal.get_params", return_value=params):
                with mock.patch("agents.skim_swarm.signal.entry_blocked_by_causation", return_value=(False, None)):
                    with mock.patch("agents.skim_swarm.signal.is_force_flatten_window", return_value=False):
                        with mock.patch("agents.skim_swarm.signal.is_eod_caution_window", return_value=False):
                            with mock.patch("agents.skim_swarm.signal.is_opening_blackout", return_value=False):
                                with mock.patch("agents.skim_swarm.signal.describe_eod_phase", return_value="normal"):
                                    d = decide(features, st, swarm_halted=False, open_positions=0, max_open=6)
        self.assertEqual(d["action"], "wait")
        self.assertIn("insufficient_buying_power_short", d["reasoning"])

    def test_min_buying_power_default(self):
        self.assertGreaterEqual(min_buying_power_for_short_usd(), 100.0)


if __name__ == "__main__":
    unittest.main()
