"""SI predictability + evolving prediction model + early infra / mega brakes."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from utils.si_predictability import (
    action_family,
    evolve_prediction_model,
    load_prediction_model,
    model_status_summary,
    predict_intervention_outcome,
    prediction_scale_multipliers,
    record_prediction,
    score_prediction_accuracy,
)
from utils.system_time import now_iso


class TestSiPredictability(unittest.TestCase):
    def test_predict_brake_is_hold(self) -> None:
        pred = predict_intervention_outcome(
            component="skim_swarm",
            action="symbol_session_brake",
            metrics={"skim_swarm": {"rolling_expectancy_usd": 0.1}},
            detail={"brakes": ["MSFT:pause"]},
        )
        self.assertEqual(pred.get("marker"), "si_predictability")
        self.assertEqual(pred.get("action_family"), "symbol_session_brake")
        self.assertIn("hold", str(pred.get("predicted_outcome") or ""))
        self.assertIn("features", pred)
        self.assertTrue(pred.get("id"))

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
                    {"skim_swarm": {"rolling_expectancy_usd": 0.11}},
                    evolve=False,
                )
        self.assertEqual(out.get("scored"), 1)
        self.assertEqual(out.get("accuracy"), 1.0)

    def test_protective_brake_hold_vs_realized_expectancy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"FORTRESS_AI_DATA_DIR": td}, clear=False):
                from utils.system_time import now

                today = now().date().isoformat()
                record_prediction(
                    {
                        "component": "skim_swarm",
                        "action": "symbol_session_brake",
                        "action_family": "symbol_session_brake",
                        "baseline_expectancy_usd": 0.18,
                        "predicted_delta_expectancy_usd": 0.0,
                        "session_date_et": today,
                    }
                )
                out = score_prediction_accuracy(
                    {"skim_swarm": {"rolling_expectancy_usd": 0.173}},
                    evolve=False,
                )
        self.assertEqual(out.get("scored"), 1)
        self.assertEqual(out.get("accuracy"), 1.0)

    def test_action_family_mid_lag(self) -> None:
        self.assertEqual(action_family("mid_lag_focus"), "mid_lag_focus")
        self.assertEqual(action_family("gap_dispatch:mid_lag_focus"), "mid_lag_focus")


class TestEvolvablePredictionModel(unittest.TestCase):
    def test_evolve_updates_family_and_scale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                "os.environ",
                {
                    "FORTRESS_AI_DATA_DIR": td,
                    "FORTRESS_SI_PREDICTABILITY": "1",
                    "FORTRESS_SI_PREDICTION_EVOLVE": "1",
                },
                clear=False,
            ):
                from utils.system_time import now

                past = (now() - timedelta(hours=2)).isoformat()
                # Seed 10 resolved-worthy predictions across a family with lifts.
                for i in range(10):
                    record_prediction(
                        {
                            "id": f"pred-{i}",
                            "ts": past,
                            "component": "skim_swarm",
                            "action": "symbol_session_brake",
                            "action_family": "symbol_session_brake",
                            "baseline_expectancy_usd": 0.10,
                            "predicted_delta_expectancy_usd": 0.0,
                            "confidence": 0.7,
                            "horizon_minutes": 60,
                            "features": {
                                "rolling_exp": 0.2,
                                "rolling_pay": 0.1,
                                "alpha_vs_spy": -0.1,
                                "strong_tape": 0.0,
                                "session_exits_norm": 0.2,
                                "open_positions_norm": 0.1,
                            },
                            "session_date_et": past[:10],
                        }
                    )
                metrics = {"skim_swarm": {"rolling_expectancy_usd": 0.12}}
                evo = evolve_prediction_model(metrics, lookback=20, max_updates=10)
                self.assertGreaterEqual(int(evo.get("updates") or 0), 8)
                model = load_prediction_model()
                fam = (model.get("action_families") or {}).get("symbol_session_brake") or {}
                self.assertGreaterEqual(int(fam.get("n") or 0), 8)
                self.assertGreater(float(fam.get("hit_rate") or 0), 0.5)
                status = model_status_summary(model)
                self.assertGreaterEqual(int(status.get("n_updates") or 0), 8)
                scale = prediction_scale_multipliers()
                self.assertIn(scale.get("mode"), ("scaled", "warmup"))
                # Second evolve must not re-process same ids.
                evo2 = evolve_prediction_model(metrics, lookback=20, max_updates=10)
                self.assertEqual(int(evo2.get("updates") or 0), 0)

    def test_action_family_gap_dispatch(self) -> None:
        self.assertEqual(action_family("gap_dispatch:symbol_session_brake"), "symbol_session_brake")
        self.assertEqual(action_family("deep_lag_wait"), "deep_lag_wait")

    def test_learned_delta_moves_on_miss(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                "os.environ",
                {"FORTRESS_AI_DATA_DIR": td, "FORTRESS_SI_PREDICTION_EVOLVE": "1"},
                clear=False,
            ):
                from utils.system_time import now

                past = (now() - timedelta(hours=3)).isoformat()
                record_prediction(
                    {
                        "id": "miss-1",
                        "ts": past,
                        "component": "infra_swarm",
                        "action": "edge_autofix",
                        "action_family": "edge_autofix",
                        "baseline_expectancy_usd": 0.20,
                        "predicted_delta_expectancy_usd": 0.02,
                        "confidence": 0.6,
                        "horizon_minutes": 30,
                        "features": {"rolling_exp": 0.4, "rolling_pay": 0.0, "alpha_vs_spy": 0.0,
                                     "strong_tape": 0.0, "session_exits_norm": 0.0, "open_positions_norm": 0.0},
                        "session_date_et": past[:10],
                    }
                )
                # Actual worsens → miss; learned_delta should decrease from prior 0.005
                before = load_prediction_model()["action_families"]["edge_autofix"]["learned_delta"]
                evolve_prediction_model(
                    {"infra_swarm": {"rolling_expectancy_usd": 0.10}},
                    lookback=5,
                    max_updates=5,
                )
                after = load_prediction_model()["action_families"]["edge_autofix"]["learned_delta"]
                self.assertLess(float(after), float(before) + 0.001)


class TestEarlyInfraTight(unittest.TestCase):
    def test_infra_tightens_before_six_exits(self) -> None:
        from utils.swarm_session_si import adapt_swarm_session

        with tempfile.TemporaryDirectory() as td:
            learned = Path(td) / "infra_swarm" / "learned"
            learned.mkdir(parents=True)
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
                                out2 = apply_symbol_session_brakes("skim_swarm")
            self.assertTrue(any("MSFT" in b and ("pause" in b or "mega_first" in b) for b in out.get("brakes") or []))
            self.assertTrue(out.get("newly_applied"))
            self.assertFalse(out2.get("newly_applied"))
            saved = json.loads((learned / "msft.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["params"].get("pause_entries"))


if __name__ == "__main__":
    unittest.main()
