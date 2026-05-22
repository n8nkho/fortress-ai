"""Trading diagnostics aggregation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from utils.trading_diagnostics import build_trading_diagnostics, _summarize_skim_decisions


class TestTradingDiagnostics(unittest.TestCase):
    def test_block_reason_counts(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            dec = data / "ai_decisions.jsonl"
            ts = datetime.now(timezone.utc).isoformat()
            row = {
                "ts": ts,
                "decision": {"action": "enter_position", "confidence": 0.7},
                "act": {"executed": False, "detail": "confidence_below_threshold:0.7<0.8"},
            }
            dec.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": td, "FORTRESS_AI_DRY_RUN": "0"}):
                with patch("utils.spy_agent_config.spy_data_dir", return_value=data):
                    with patch("utils.skim_swarm_config.swarm_data_dir", return_value=data):
                        out = build_trading_diagnostics(days=14)
            ai = out["fortress_ai"]
            self.assertGreaterEqual(ai["cycles"], 1)
            self.assertIn("confidence_below_threshold", ai.get("block_reason_counts", {}))

    def test_skim_block_reason_counts(self):
        ts = datetime.now(timezone.utc).isoformat()
        wave = {
            "ts": ts,
            "wave": 1,
            "open_positions": 2,
            "results": [
                {
                    "symbol": "NVDA",
                    "features": {"side": "flat"},
                    "decision": {"action": "wait", "reasoning": "max_open_positions"},
                    "act": {"executed": False, "block_reason": "max_open_positions"},
                },
                {
                    "symbol": "MSFT",
                    "features": {"side": "flat"},
                    "decision": {"action": "enter_long", "reasoning": "pullback_uptrend score=0.30"},
                    "act": {"executed": False, "block_reason": "broker_error"},
                },
            ],
        }
        out = _summarize_skim_decisions([wave], days=14)
        self.assertEqual(out["waves"], 1)
        self.assertEqual(out["entry_proposed"], 1)
        self.assertIn("max_open_positions", out["block_reason_counts"])
        self.assertIn("broker_error", out["block_reason_counts"])


if __name__ == "__main__":
    unittest.main()
