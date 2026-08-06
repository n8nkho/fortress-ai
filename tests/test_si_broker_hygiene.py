"""SI broker hygiene + decision trail denylist truth."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.system_time import now


class TestBrokerHygiene(unittest.TestCase):
    def test_pauses_repeat_broker_error_symbol(self) -> None:
        from utils.si_broker_hygiene import apply_broker_error_symbol_brakes

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            dec = data / "skim_swarm" / "decisions.jsonl"
            dec.parent.mkdir(parents=True)
            today = now().date().isoformat()
            # Two waves with broker_error on QQQ
            for i in range(3):
                row = {
                    "ts": f"{today}T14:0{i}:00-04:00",
                    "session_date_et": today,
                    "results": [
                        {
                            "symbol": "QQQ",
                            "decision": {"symbol": "QQQ", "action": "enter_long", "reasoning": "ok"},
                            "act": {
                                "executed": False,
                                "block_reason": "broker_error",
                                "detail": "broker_error:Timeout",
                            },
                        }
                    ],
                }
                with dec.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch(
                    "utils.si_capability_review.collect_metrics",
                    return_value={"skim_swarm": {"rolling_expectancy_usd": 0.1}},
                ):
                    out = apply_broker_error_symbol_brakes("skim_swarm")
            self.assertTrue(out.get("newly_applied") or out.get("brakes"))
            learned = json.loads((data / "skim_swarm" / "learned" / "qqq.json").read_text())
            self.assertTrue(learned["params"]["pause_entries"])


class TestDenylistDecisionTrail(unittest.TestCase):
    def test_audit_sees_results_array_denylist(self) -> None:
        from utils.si_participation_actions import audit_denylist_vs_universe

        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            for sleeve in ("skim_swarm", "infra_swarm"):
                d = data / sleeve
                d.mkdir(parents=True)
                (d / "runtime_overrides.json").write_text("{}", encoding="utf-8")
            today = now().date().isoformat()
            row = {
                "ts": f"{today}T14:00:00-04:00",
                "session_date_et": today,
                "results": [
                    {
                        "symbol": "MA",
                        "decision": {"symbol": "MA", "reasoning": "manual_denylist"},
                        "act": {"block_reason": "manual_denylist"},
                    }
                ],
            }
            with (data / "skim_swarm" / "decisions.jsonl").open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch("utils.skim_swarm_config.universe", return_value=["MA", "MSFT"]):
                    with patch("utils.infra_swarm_config.universe", return_value=["NVDA"]):
                        with patch("utils.skim_swarm_config.runtime_denylist", return_value=frozenset()):
                            with patch("utils.infra_swarm_config.runtime_denylist", return_value=frozenset()):
                                out = audit_denylist_vs_universe()
            self.assertIn("MA", out.get("skim_decision_blocked") or [])
            self.assertIn("MA", out.get("skim_blocked_in_universe") or [])


if __name__ == "__main__":
    unittest.main()
