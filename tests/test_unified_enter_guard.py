"""Unified AI enter cooldown dedup."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.unified_enter_guard import (
    enter_cooldown_sec,
    entry_blocked_by_cooldown,
    load_state,
    record_enter,
    record_exit,
    save_state,
)


class TestUnifiedEnterGuard(unittest.TestCase):
    def test_blocks_reentry_within_cooldown_when_flat(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": td, "FORTRESS_UNIFIED_ENTER_COOLDOWN_SEC": "900"}):
                record_enter("HRL")
                blocked, reason = entry_blocked_by_cooldown("HRL", held_qty=0)
                self.assertTrue(blocked)
                self.assertEqual(reason.split(":")[0], "enter_cooldown")

    def test_allows_reentry_after_exit(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"FORTRESS_AI_DATA_DIR": td, "FORTRESS_UNIFIED_ENTER_COOLDOWN_SEC": "900"}):
                record_enter("HRL")
                record_exit("HRL")
                blocked, reason = entry_blocked_by_cooldown("HRL", held_qty=0)
                self.assertFalse(blocked)

    def test_already_holding_when_qty_positive(self):
        blocked, reason = entry_blocked_by_cooldown("IBM", held_qty=10)
        self.assertTrue(blocked)
        self.assertEqual(reason, "already_holding")


class TestUnifiedActEnterCooldown(unittest.TestCase):
    def test_act_blocks_enter_cooldown(self):
        mock_ticker = MagicMock()
        mock_ticker.fast_info.get.return_value = 30.0
        with patch("agents.unified_ai_agent._dry_run", return_value=False):
            with patch("agents.unified_ai_agent._min_confidence_execute", return_value=0.5):
                with patch("agents.unified_ai_agent.yf.Ticker", return_value=mock_ticker):
                    with patch(
                        "utils.unified_enter_guard.entry_blocked_by_cooldown",
                        return_value=(True, "enter_cooldown:HRL:120s"),
                    ):
                        from agents.unified_ai_agent import act

                        result = act(
                            {
                                "action": "enter_position",
                                "confidence": 0.9,
                                "parameters": {"symbol": "HRL", "qty": 10},
                            },
                            {"equity": 100_000.0, "positions": []},
                            {},
                        )
                        self.assertFalse(result.get("executed"))
                        self.assertEqual(result.get("block_reason"), "enter_cooldown")


if __name__ == "__main__":
    unittest.main()
