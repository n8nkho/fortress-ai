"""PnL extraction and shadow metrics helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestDecisionLogMetrics(unittest.TestCase):
    def test_extract_pnl_nested(self):
        from utils.decision_log_metrics import extract_pnl_usd

        row = {"act": {"detail": {"pnl": "12.5"}}}
        self.assertEqual(extract_pnl_usd(row), 12.5)

    def test_win_rate_and_drawdown(self):
        from utils.decision_log_metrics import max_drawdown_fraction, win_rate_from_pnls

        self.assertEqual(win_rate_from_pnls([10.0, -5.0, 3.0]), 2 / 3)
        self.assertIsNotNone(max_drawdown_fraction([10.0, -30.0, 5.0]))


if __name__ == "__main__":
    unittest.main()
