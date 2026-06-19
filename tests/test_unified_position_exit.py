import unittest
from unittest import mock

from utils.unified_position_exit import (
    compute_rsi,
    plan_profit_exits,
    resolve_adaptive_thresholds,
)


class TestUnifiedPositionExit(unittest.TestCase):
    def test_take_profit_usd(self):
        positions = [
            {
                "sym": "QQQ",
                "qty": 4,
                "unrealized_pl": 45.0,
                "unrealized_plpc": 0.004,
            }
        ]
        with mock.patch("utils.unified_position_exit._minutes_to_rth_close", return_value=None):
            with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=55.0):
                plans = plan_profit_exits(positions, {"vix_last": 18})
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["sym"], "QQQ")
        self.assertEqual(plans[0]["qty"], 4)
        self.assertIn("take_profit_usd", plans[0]["reason"])

    def test_take_profit_pct(self):
        positions = [
            {
                "sym": "IWM",
                "qty": 10,
                "unrealized_pl": 15.0,
                "unrealized_plpc": 0.006,
            }
        ]
        with mock.patch("utils.unified_position_exit._minutes_to_rth_close", return_value=None):
            with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=50.0):
                plans = plan_profit_exits(positions, {"vix_last": 18})
        self.assertEqual(len(plans), 1)
        self.assertIn("take_profit_pct", plans[0]["reason"])

    def test_rsi_overbought_exit(self):
        positions = [
            {
                "sym": "ASML",
                "qty": 2,
                "unrealized_pl": 12.0,
                "unrealized_plpc": 0.003,
            }
        ]
        with mock.patch("utils.unified_position_exit._minutes_to_rth_close", return_value=None):
            with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=68.0):
                plans = plan_profit_exits(positions, {"vix_last": 18})
        self.assertEqual(len(plans), 1)
        self.assertIn("rsi_overbought_exit", plans[0]["reason"])
        self.assertEqual(plans[0]["block_reason_marker"], "rsi_overbought_exit")

    def test_rsi_extreme_overbought_small_profit(self):
        positions = [
            {
                "sym": "MDT",
                "qty": 5,
                "unrealized_pl": 9.0,
                "unrealized_plpc": 0.002,
            }
        ]
        with mock.patch("utils.unified_position_exit._minutes_to_rth_close", return_value=None):
            with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=74.0):
                plans = plan_profit_exits(positions, {"vix_last": 18})
        self.assertEqual(len(plans), 1)
        self.assertIn("rsi_extreme_overbought", plans[0]["reason"])

    def test_adaptive_stop_loss_bear(self):
        positions = [
            {
                "sym": "QQQ",
                "qty": 4,
                "unrealized_pl": -10.0,
                "unrealized_plpc": -0.018,
            }
        ]
        with mock.patch("utils.unified_position_exit._minutes_to_rth_close", return_value=None):
            with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=40.0):
                plans = plan_profit_exits(positions, {"vix_last": 28})
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["block_reason_marker"], "adaptive_stop_loss")

    def test_skips_loser_without_stop(self):
        positions = [{"sym": "MDT", "qty": 10, "unrealized_pl": -5.0, "unrealized_plpc": -0.01}]
        with mock.patch("utils.unified_position_exit._minutes_to_rth_close", return_value=None):
            with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=50.0):
                plans = plan_profit_exits(positions, {"vix_last": 18})
        self.assertEqual(plans, [])

    def test_sorts_by_profit_desc(self):
        positions = [
            {"sym": "A", "qty": 1, "unrealized_pl": 25.0, "unrealized_plpc": 0.01},
            {"sym": "B", "qty": 1, "unrealized_pl": 80.0, "unrealized_plpc": 0.02},
        ]
        with mock.patch("utils.unified_position_exit.fetch_symbol_rsi", return_value=50.0):
            plans = plan_profit_exits(positions)
        self.assertEqual([p["sym"] for p in plans], ["B", "A"])

    def test_bear_regime_lowers_profit_bar(self):
        cfg = {
            "min_profit_usd": 20.0,
            "min_profit_pct": 0.005,
            "eod_flatten_minutes_before_close": 20.0,
            "rsi_period": 14.0,
            "rsi_extreme_min_usd": 8.0,
            "rsi_extreme_level": 72.0,
            "stop_loss_pct": 0.02,
            "stop_loss_enabled": True,
            "bear_min_usd_scale": 0.75,
            "bear_min_pct_scale": 0.75,
            "bear_rsi_exit_delta": 3.0,
            "bear_eod_scale": 1.25,
            "bear_stop_tighten": 0.85,
            "bull_min_usd_scale": 1.15,
            "bull_min_pct_scale": 1.1,
            "bull_rsi_exit_delta": 3.0,
            "close_phase_eod_min": 25.0,
        }
        neutral = resolve_adaptive_thresholds({"vix_last": 18}, cfg)
        bear = resolve_adaptive_thresholds({"vix_last": 28}, cfg)
        self.assertLess(bear["min_profit_usd"], neutral["min_profit_usd"])
        self.assertLess(bear["rsi_exit_threshold"], neutral["rsi_exit_threshold"])

    def test_compute_rsi(self):
        # Flat then up — RSI should rise above 50
        closes = [100.0 + (i * 0.5 if i > 14 else 0) for i in range(30)]
        rsi = compute_rsi(closes, 14)
        self.assertIsNotNone(rsi)
        assert rsi is not None
        self.assertGreater(rsi, 50.0)


if __name__ == "__main__":
    unittest.main()
