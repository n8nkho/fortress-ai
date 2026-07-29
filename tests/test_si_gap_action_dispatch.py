"""Gap→action dispatch makes SI act on objective gaps."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_gap_action_dispatch import (
    dispatch_gap_actions,
    ensure_effectiveness_actions,
)
from utils.si_recommendation_queue import is_cross_stack_item


class TestCrossStackNative(unittest.TestCase):
    def test_capability_review_skim_gap_not_cross_stack(self) -> None:
        self.assertFalse(
            is_cross_stack_item(
                {
                    "code": "skim_payoff_ratio_gap",
                    "source": "capability_review",
                    "cross_stack": True,
                }
            )
        )

    def test_classic_mirror_is_cross_stack(self) -> None:
        self.assertTrue(
            is_cross_stack_item(
                {
                    "code": "classic_mirror_classic_fill_recency",
                    "source": "integrity_scan",
                    "cross_stack": False,
                }
            )
        )


class TestGapActionDispatch(unittest.TestCase):
    def test_dispatch_calls_brake_for_effectiveness_gap(self) -> None:
        with patch(
            "utils.si_gap_action_dispatch._invoke_action",
            return_value={
                "action": "symbol_session_brake",
                "ok": True,
                "result": {"brakes": ["PLTR:pause_entries"], "marker": "symbol_session_brake"},
            },
        ) as inv:
            with patch("utils.si_intervention_log.record_intervention") as rec:
                with patch(
                    "utils.si_capability_review.collect_metrics",
                    return_value={"skim_swarm": {"rolling_expectancy_usd": 0.1}},
                ):
                    out = dispatch_gap_actions(
                        force_codes=["si_intervention_effectiveness_gap"],
                        components=("skim_swarm",),
                        record=True,
                    )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("marker"), "gap_action_dispatch")
        self.assertGreaterEqual(int(out.get("scoreable_hits") or 0), 1)
        self.assertTrue(inv.called)
        self.assertTrue(rec.called)

    def test_ensure_effectiveness_forces_when_rate_none(self) -> None:
        with patch(
            "utils.si_intervention_log.intervention_success_rate",
            return_value=None,
        ):
            with patch(
                "utils.si_capability_review.collect_metrics",
                return_value={
                    "skim_swarm": {
                        "rolling_payoff_ratio": 0.9,
                        "rolling_expectancy_usd": 0.05,
                    }
                },
            ):
                with patch(
                    "utils.si_gap_action_dispatch.dispatch_gap_actions",
                    return_value={"ok": True, "forced": True},
                ) as disp:
                    out = ensure_effectiveness_actions()
        self.assertTrue(out.get("ok"))
        args, kwargs = disp.call_args
        codes = kwargs.get("force_codes") or []
        self.assertIn("si_intervention_effectiveness_gap", codes)
        self.assertIn("skim_payoff_ratio_gap", codes)


class TestSymbolBleederBrake(unittest.TestCase):
    def test_small_bleeder_brakes_despite_large_winner(self) -> None:
        from utils.si_adaptive_actions import apply_symbol_session_brakes

        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "skim_swarm" / "learned"
            learned.mkdir(parents=True)
            (learned / "aiq.json").write_text(
                json.dumps(
                    {
                        "session_date_et": "2099-01-02",
                        "session_stats": {"exits": 4, "sum_pnl_usd": 2.34},
                        "params": {},
                    }
                ),
                encoding="utf-8",
            )
            (learned / "pltr.json").write_text(
                json.dumps(
                    {
                        "session_date_et": "2099-01-02",
                        "session_stats": {"exits": 2, "sum_pnl_usd": -0.205},
                        "params": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_SI_ADAPTIVE_ACTIONS": "1",
                    "FORTRESS_SI_SYMBOL_BRAKE_BLEEDER_USD": "0.15",
                },
                clear=False,
            ):
                with patch(
                    "utils.si_adaptive_actions._session_date",
                    return_value="2099-01-02",
                ):
                    with patch(
                        "utils.si_adaptive_actions._rolling_symbol_loss_hits",
                        return_value=[],
                    ):
                        out = apply_symbol_session_brakes("skim_swarm")
            self.assertTrue(out.get("brakes"))
            self.assertTrue(any("PLTR" in b for b in out["brakes"]))


if __name__ == "__main__":
    unittest.main()
