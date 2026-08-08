"""Continuous SI cycle unit tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestContinuousSiHygiene(unittest.TestCase):
    def test_close_stale_not_worth(self) -> None:
        from utils.si_continuous_cycle import close_stale_not_worth_open

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "si_recommendation_queue.json"
            p.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "a",
                                "code": "classic_mirror_x",
                                "status": "open",
                                "disposition": "pending_agent_review",
                                "agent_assessment": {"worth_implementing": False},
                            },
                            {
                                "id": "b",
                                "code": "real_fix",
                                "status": "open",
                                "disposition": "auto_implement_queued",
                                "agent_assessment": {"worth_implementing": True},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                closed = close_stale_not_worth_open()
            doc = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(closed, ["classic_mirror_x"])
        self.assertEqual(doc["items"][0]["status"], "closed")
        self.assertEqual(doc["items"][1]["status"], "open")

    def test_reset_retriable_blocked(self) -> None:
        from utils.si_continuous_cycle import reset_retriable_blocked_auto_implement

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "si_recommendation_queue.json"
            p.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "c",
                                "code": "premature_exit_ledger",
                                "status": "open",
                                "disposition": "auto_implement_queued",
                                "code_implementation": {
                                    "status": "blocked",
                                    "error": "outside_allowlist:data/infra_swarm/x.json",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                reset = reset_retriable_blocked_auto_implement()
            doc = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(reset, ["premature_exit_ledger"])
        self.assertEqual(doc["items"][0]["code_implementation"]["status"], "retry_pending")


if __name__ == "__main__":
    unittest.main()
