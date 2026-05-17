"""Comparison metrics (realized P&L from ledgers)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestComparisonMetrics(unittest.TestCase):
    def test_read_pnl_ledger_summary(self):
        from utils.classic_bridge import read_pnl_ledger_summary

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pnl_ledger.jsonl"
            p.write_text(
                "\n".join(
                    [
                        json.dumps({"pnl": 10.0}),
                        json.dumps({"pnl": -3.0}),
                        json.dumps({"pnl": 5.0}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            s = read_pnl_ledger_summary(p)
            self.assertEqual(s["count"], 3)
            self.assertEqual(s["realized_pnl"], 12.0)
            self.assertAlmostEqual(s["win_rate"], 2 / 3, places=3)

    def test_comparison_chart_series(self):
        from utils.comparison_metrics import comparison_chart_series

        c = comparison_chart_series(
            classic_realized=100.0,
            classic_unrealized=-5.0,
            ai_realized=0.0,
            ai_unrealized=0.0,
        )
        self.assertEqual(c["labels"], ["Classic", "Fortress AI"])
        self.assertEqual(c["realized_usd"][0], 100.0)


if __name__ == "__main__":
    unittest.main()
