"""Trading diagnostics aggregation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from utils.trading_diagnostics import build_trading_diagnostics


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
                    out = build_trading_diagnostics(days=14)
            ai = out["fortress_ai"]
            self.assertGreaterEqual(ai["cycles"], 1)
            self.assertIn("confidence_below_threshold", ai.get("block_reason_counts", {}))


if __name__ == "__main__":
    unittest.main()
