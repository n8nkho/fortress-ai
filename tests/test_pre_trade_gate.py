"""Pre-trade gate — position size % vs equity."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


class TestPreTradeGatePositionPct(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self._prev_data = os.environ.get("FORTRESS_AI_DATA_DIR")
        os.environ["FORTRESS_AI_DATA_DIR"] = self._td

    def tearDown(self):
        if self._prev_data is None:
            os.environ.pop("FORTRESS_AI_DATA_DIR", None)
        else:
            os.environ["FORTRESS_AI_DATA_DIR"] = self._prev_data
        shutil.rmtree(self._td, ignore_errors=True)

    def test_buy_blocked_when_notional_exceeds_equity_times_pct(self):
        import importlib

        import utils.pre_trade_gate as gmod
        import utils.tunable_overrides as tov

        importlib.reload(tov)
        importlib.reload(gmod)

        from utils.pre_trade_gate import evaluate_pre_trade_submission

        # 3% of 100k = 3k max notional from position cap (env notional cap is higher)
        g = evaluate_pre_trade_submission(
            side="BUY",
            symbol="AAPL",
            qty=100,
            estimated_notional_usd=5000.0,
            portfolio_equity_usd=100000.0,
        )
        self.assertFalse(g["allowed"])
        self.assertTrue(any("estimated_notional_exceeds_cap" in r for r in g["reasons"]))


if __name__ == "__main__":
    unittest.main()
