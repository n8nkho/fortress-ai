"""SI predictability + early infra tight + mega-cap first-loss brake."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.si_predictability import (
    predict_intervention_outcome,
    score_prediction_accuracy,
    record_prediction,
)


class TestSiPredictability(unittest.TestCase):
    def test_predict_brake_is_hold(self) -> None:
        pred = predict_intervention_outcome(
            component="skim_swarm",
            action="symbol_session_brake",
            metrics={"skim_swarm": {"rolling_expectancy_usd": 0.1}},
            detail={"brakes": ["MSFT:pause"]},
        )
        self.assertEqual(pred.get("marker"), "si_predictability")
        self.assertIn("hold", str(pred.get("predicted_outcome") or ""))

    def test_score_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                record_prediction(
                    {
                        "component": "skim_swarm",
                        "action": "symbol_session_brake",
                        "baseline_expectancy_usd": 0.10,
                        "predicted_delta_expectancy_usd": 0.0,
                        "session_date_et": "2099-01-01",
                    }
                )
                out = score_prediction_accuracy(
                    {"skim_swarm": {"rolling_expectancy_usd": 0.11}}
                )
        self.assertEqual(out.get("scored"), 1)
        self.assertEqual(out.get("accuracy"), 1.0)


class TestEarlyInfraTight(unittest.TestCase):
    def test_infra_tightens_before_six_exits(self) -> None:
        from utils.swarm_session_si import adapt_swarm_session

        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "infra_swarm" / "learned"
            learned.mkdir(parents=True)
            # 4 exits, negative expectancy — previously needed 6 for infra.
            (learned / "avgo.json").write_text(
                json.dumps(
                    {
                        "session_date_et": "2099-01-03",
                        "session_stats": {
                            "exits": 2,
                            "wins": 0,
                            "losses": 2,
                            "sum_pnl_usd": -0.42,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (learned / "smci.json").write_text(
                json.dumps(
                    {
                        "session_date_et": "2099-01-03",
                        "session_stats": {
                            "exits": 2,
                            "wins": 0,
                            "losses": 2,
                            "sum_pnl_usd": -0.30,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_INFRA_SWARM_SESSION_SI": "1",
                    "FORTRESS_SI_EARLY_TIGHT_MIN_EXITS": "3",
                },
                clear=False,
            ):
                with patch(
                    "utils.swarm_session_si._session_date",
                    return_value="2099-01-03",
                ):
                    with patch(
                        "utils.infra_swarm_config.session_expectancy_min_usd",
                        return_value=-0.03,
                    ):
                        with patch(
                            "utils.infra_swarm_config.max_open_positions",
                            return_value=4,
                        ):
                            with patch(
                                "utils.infra_swarm_config.daily_stop_usd",
                                return_value=-25.0,
                            ):
                                with patch(
                                    "utils.infra_swarm_config.max_l1_gross_long",
                                    return_value=3,
                                ):
                                    pol = adapt_swarm_session("infra_swarm")
            self.assertEqual(pol.get("mode"), "tight")
            notes = " ".join(pol.get("notes") or [])
            self.assertIn("early_negative_edge", notes)


class TestMegaCapFirstLoss(unittest.TestCase):
    def test_msft_first_loss_pauses(self) -> None:
        from utils.si_adaptive_actions import apply_symbol_session_brakes

        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "skim_swarm" / "learned"
            learned.mkdir(parents=True)
            (learned / "msft.json").write_text(
                json.dumps(
                    {
                        "session_date_et": "2099-01-04",
                        "session_stats": {"exits": 1, "sum_pnl_usd": -0.45},
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
                    "FORTRESS_SI_MEGA_CAP_FIRST_LOSS_USD": "0.40",
                },
                clear=False,
            ):
                with patch(
                    "utils.si_adaptive_actions._session_date",
                    return_value="2099-01-04",
                ):
                    with patch(
                        "utils.si_adaptive_actions._get_cap",
                        return_value=1.0,
                    ):
                        with patch(
                            "utils.si_adaptive_actions._rolling_symbol_loss_hits",
                            return_value=[],
                        ):
                            with patch(
                                "utils.si_capability_review.collect_metrics",
                                return_value={"skim_swarm": {"rolling_expectancy_usd": 0.1}},
                            ):
                                out = apply_symbol_session_brakes("skim_swarm")
                                # Second call should not re-record newly_applied
                                out2 = apply_symbol_session_brakes("skim_swarm")
            self.assertTrue(any("MSFT" in b and ("pause" in b or "mega_first" in b) for b in out.get("brakes") or []))
            self.assertTrue(out.get("newly_applied"))
            self.assertFalse(out2.get("newly_applied"))
            saved = json.loads((learned / "msft.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["params"].get("pause_entries"))


if __name__ == "__main__":
    unittest.main()
