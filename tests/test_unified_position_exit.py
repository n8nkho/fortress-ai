import unittest
from unittest import mock

from utils.unified_position_exit import plan_profit_exits


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
            plans = plan_profit_exits(positions)
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
            plans = plan_profit_exits(positions)
        self.assertEqual(len(plans), 1)
        self.assertIn("take_profit_pct", plans[0]["reason"])

    def test_skips_loser(self):
        positions = [{"sym": "MDT", "qty": 10, "unrealized_pl": -5.0, "unrealized_plpc": -0.01}]
        self.assertEqual(plan_profit_exits(positions), [])

    def test_sorts_by_profit_desc(self):
        positions = [
            {"sym": "A", "qty": 1, "unrealized_pl": 25.0, "unrealized_plpc": 0.01},
            {"sym": "B", "qty": 1, "unrealized_pl": 80.0, "unrealized_plpc": 0.02},
        ]
        plans = plan_profit_exits(positions)
        self.assertEqual([p["sym"] for p in plans], ["B", "A"])


if __name__ == "__main__":
    unittest.main()
