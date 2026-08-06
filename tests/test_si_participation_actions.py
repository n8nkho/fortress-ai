"""SI participation policy — deep lag, denylist, infra soft path."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_participation_actions import (
    apply_deep_lag_wait_strategy,
    apply_infra_strong_tape_soft_path,
    ensure_participation_policy_session,
    run_participation_si_cycle,
)


class TestDeepLagWait(unittest.TestCase):
    def test_deep_lag_strategy_on_strong_tape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch(
                    "utils.si_participation_actions.audit_denylist_vs_universe",
                    return_value={
                        "ok": True,
                        "skim_blocked_in_universe": ["XYZ"],
                        "infra_blocked_in_universe": [],
                        "thaw_candidates": [{"symbol": "XYZ", "component": "skim_swarm", "expectancy_usd": 0.2}],
                    },
                ):
                    with patch(
                        "utils.si_capability_review.collect_metrics",
                        return_value={"portfolio_session": {"alpha_vs_spy_pct": -2.0}},
                    ):
                        with patch("utils.swarm_session_si.load_session_policy", return_value={}):
                            with patch("utils.swarm_session_si.save_session_policy"):
                                out = apply_deep_lag_wait_strategy(
                                    port={
                                        "strong_tape_1d": True,
                                        "alpha_vs_spy_pct": -2.0,
                                        "participation_shortfall_exits": 6,
                                    }
                                )
        self.assertEqual(out.get("strategy"), "deep_lag_wait")
        self.assertEqual(out.get("marker"), "si_deep_lag_wait")

    def test_not_deep_lag_skips(self) -> None:
        out = apply_deep_lag_wait_strategy(
            port={"strong_tape_1d": True, "alpha_vs_spy_pct": -0.2}
        )
        self.assertEqual(out.get("skipped"), "not_deep_lag")


class TestSessionRollover(unittest.TestCase):
    def test_rollover_clears_stale_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "si_capability" / "participation_policy.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "session_date_et": "2026-08-04",
                        "strategy": "deep_lag_wait",
                        "events": [{"strategy": "deep_lag_wait"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch("utils.si_participation_actions.now") as n:
                    from datetime import datetime
                    from zoneinfo import ZoneInfo

                    n.return_value = datetime(2026, 8, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
                    pol = ensure_participation_policy_session()
            self.assertEqual(pol.get("session_date_et"), "2026-08-05")
            self.assertIsNone(pol.get("strategy"))
            self.assertEqual(pol.get("rollover_from"), "2026-08-04")


class TestInfraSoftPath(unittest.TestCase):
    def test_soft_path_when_idle_strong_tape_and_near_score(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pol_path = Path(td) / "infra_swarm" / "session_policy.json"
            pol_path.parent.mkdir(parents=True)
            pol_path.write_text(json.dumps({"mode": "normal", "session_exits": 0}), encoding="utf-8")
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch(
                    "utils.si_capability_review.collect_metrics",
                    return_value={"infra_swarm": {"rolling_expectancy_usd": 0.1}},
                ):
                    with patch(
                        "utils.si_participation_actions._infra_near_entry_threshold",
                        return_value={"near": True, "max_score": 0.08, "floor": -0.05},
                    ):
                        with patch(
                            "utils.si_participation_actions._infra_session_exits",
                            return_value=0,
                        ):
                            out = apply_infra_strong_tape_soft_path(
                                port={
                                    "strong_tape_1d": True,
                                    "session_exit_count": 0,
                                    "alpha_vs_spy_pct": -0.5,
                                }
                            )
                            out2 = apply_infra_strong_tape_soft_path(
                                port={
                                    "strong_tape_1d": True,
                                    "session_exit_count": 0,
                                    "alpha_vs_spy_pct": -0.5,
                                }
                            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("marker"), "si_infra_strong_tape_soft")
        self.assertLessEqual(float(out.get("enter_long_delta_boost") or 0), 0)
        self.assertEqual(out2.get("skipped"), "already_applied_session")

    def test_soft_path_blocked_by_deep_lag(self) -> None:
        with patch("utils.si_participation_actions._infra_session_exits", return_value=0):
            out = apply_infra_strong_tape_soft_path(
                port={
                    "strong_tape_1d": True,
                    "session_exit_count": 0,
                    "alpha_vs_spy_pct": -2.0,
                }
            )
        self.assertEqual(out.get("skipped"), "deep_lag_blocks_soft_path")

    def test_soft_path_skips_without_near_score(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                with patch(
                    "utils.si_participation_actions._infra_near_entry_threshold",
                    return_value={"near": False, "reason": "scores_too_weak", "max_score": -0.2},
                ):
                    with patch(
                        "utils.si_participation_actions._infra_session_exits",
                        return_value=0,
                    ):
                        out = apply_infra_strong_tape_soft_path(
                            port={
                                "strong_tape_1d": True,
                                "session_exit_count": 0,
                                "alpha_vs_spy_pct": -0.3,
                            }
                        )
        self.assertEqual(out.get("skipped"), "not_near_entry_threshold")


class TestParticipationCycle(unittest.TestCase):
    def test_cycle_routes_deep_lag(self) -> None:
        with patch(
            "utils.si_participation_actions.apply_deep_lag_wait_strategy",
            return_value={"ok": True, "strategy": "deep_lag_wait", "audit": {}},
        ) as deep:
            with patch(
                "utils.si_participation_actions.ensure_participation_policy_session",
                return_value={"session_date_et": "2026-08-05"},
            ):
                with patch(
                    "utils.si_participation_actions._portfolio",
                    return_value={
                        "strong_tape_1d": True,
                        "alpha_vs_spy_pct": -2.0,
                        "session_exit_count": 0,
                    },
                ):
                    out = run_participation_si_cycle()
        self.assertTrue(deep.called)
        self.assertEqual((out.get("deep_lag") or {}).get("strategy"), "deep_lag_wait")


class TestDeployedQueueNoise(unittest.TestCase):
    def test_reconcile_closes_deployed_auto_implement(self) -> None:
        from utils.si_recommendation_queue import (
            DISPOSITION_AUTO_IMPLEMENT_QUEUED,
            STATUS_OPEN,
            reconcile_deployed_guards,
        )

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "si_recommendation_queue.json"
            p.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "1",
                                "code": "market_relative_underperformance",
                                "status": STATUS_OPEN,
                                "disposition": DISPOSITION_AUTO_IMPLEMENT_QUEUED,
                                "cross_stack": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            import utils.si_recommendation_queue as qmod

            orig = qmod.queue_path
            qmod.queue_path = lambda: p  # type: ignore[assignment]
            try:
                with patch("utils.si_fix_deployment.is_deployed", return_value=True):
                    closed = reconcile_deployed_guards(
                        {"findings": [{"code": "market_relative_underperformance", "severity": "high"}]}
                    )
                doc = json.loads(p.read_text(encoding="utf-8"))
            finally:
                qmod.queue_path = orig  # type: ignore[assignment]
            self.assertIn("market_relative_underperformance", closed)
            self.assertEqual(doc["items"][0]["closed_reason"], "deployed_guard_auto_implement_noise")


class TestBrokerErrorSpike(unittest.TestCase):
    def test_scan_broker_error_spike(self) -> None:
        from utils.integrity_diagnostics import scan_broker_error_spike

        out = scan_broker_error_spike(
            component="skim_swarm",
            blocks={"broker_error": 5},
            rows=[],
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["code"], "broker_error_session_spike")


if __name__ == "__main__":
    unittest.main()
