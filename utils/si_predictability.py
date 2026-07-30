"""SI predictability — forecast intervention outcomes and score hit rate.

Makes SI behavior inspectable: each material action logs a predicted expectancy
delta / hold outcome; later cycles score prediction accuracy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.system_time import now, now_iso

_MARKER = "si_predictability"


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def predictability_log_path() -> Path:
    return _data_dir() / "si_capability" / "predictions.jsonl"


def predictability_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_PREDICTABILITY", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def predict_intervention_outcome(
    *,
    component: str,
    action: str,
    metrics: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded heuristic forecast — never loosens rails."""
    metrics = metrics or {}
    detail = detail or {}
    comp = metrics.get(component) or {}
    exp = comp.get("rolling_expectancy_usd")
    if exp is None:
        exp = comp.get("session_expectancy_usd")
    try:
        exp_f = float(exp) if exp is not None else 0.0
    except (TypeError, ValueError):
        exp_f = 0.0

    action_l = str(action or "")
    # Protective actions: predict hold (delta ~ 0) with high confidence when already healthy.
    if action_l in ("symbol_session_brake", "swarm_session_tight", "swarm_session_churn") or action_l.startswith(
        "gap_dispatch:"
    ):
        predicted_delta = 0.0 if exp_f >= 0 else 0.01
        outcome = "hold_or_improve_expectancy"
        confidence = 0.72 if "brake" in action_l or "pause" in str(detail) else 0.6
    elif action_l == "edge_autofix":
        predicted_delta = 0.005
        outcome = "slight_expectancy_lift"
        confidence = 0.45
    elif action_l == "swarm_session_normal":
        predicted_delta = 0.0
        outcome = "maintain_baseline"
        confidence = 0.5
    elif action_l == "constructive_tape_override":
        predicted_delta = 0.0
        outcome = "participation_without_deep_alpha_breach"
        confidence = 0.55
    else:
        predicted_delta = 0.0
        outcome = "neutral"
        confidence = 0.4

    return {
        "component": component,
        "action": action,
        "predicted_delta_expectancy_usd": round(predicted_delta, 4),
        "predicted_outcome": outcome,
        "confidence": confidence,
        "baseline_expectancy_usd": round(exp_f, 4),
        "horizon_minutes": 60,
        "marker": _MARKER,
        "ts": now_iso(),
        "session_date_et": now().date().isoformat(),
    }


def record_prediction(prediction: dict[str, Any]) -> None:
    if not predictability_enabled():
        return
    p = predictability_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(prediction, default=str) + "\n")


def _read_tail(path: Path, *, max_bytes: int = 256_000) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
            if isinstance(doc, dict):
                out.append(doc)
        except Exception:
            continue
    return out


def score_prediction_accuracy(
    metrics: dict[str, Any],
    *,
    lookback: int = 24,
) -> dict[str, Any]:
    """Fraction of recent predictions whose expectancy outcome matched (held/improved)."""
    rows = _read_tail(predictability_log_path())[-lookback:]
    if not rows:
        return {
            "accuracy": None,
            "scored": 0,
            "marker": _MARKER,
            "note": "no_predictions",
        }

    scored = 0
    hits = 0
    for row in rows:
        if row.get("scoreable") is False:
            continue
        comp = str(row.get("component") or "")
        if not comp or comp == "si_meta":
            continue
        baseline = row.get("baseline_expectancy_usd")
        pred_delta = row.get("predicted_delta_expectancy_usd")
        if baseline is None:
            continue
        after = (metrics.get(comp) or {}).get("rolling_expectancy_usd")
        if after is None:
            after = (metrics.get(comp) or {}).get("session_expectancy_usd")
        if after is None:
            continue
        try:
            baseline_f = float(baseline)
            after_f = float(after)
            pred_d = float(pred_delta or 0)
        except (TypeError, ValueError):
            continue
        scored += 1
        # Hit if actual change is not worse than predicted by > 1¢ (protective bias).
        actual_delta = after_f - baseline_f
        if actual_delta >= pred_d - 0.01:
            hits += 1

    return {
        "accuracy": round(hits / scored, 4) if scored else None,
        "scored": scored,
        "hits": hits,
        "marker": _MARKER,
        "target_min": 0.55,
    }


def attach_prediction_to_intervention(
    *,
    component: str,
    action: str,
    metrics_snapshot: dict[str, Any] | None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Predict + persist; return prediction dict for embedding in intervention detail."""
    pred = predict_intervention_outcome(
        component=component,
        action=action,
        metrics=metrics_snapshot,
        detail=detail,
    )
    try:
        record_prediction(pred)
    except Exception:
        pass
    return pred


__all__ = [
    "attach_prediction_to_intervention",
    "predict_intervention_outcome",
    "predictability_enabled",
    "record_prediction",
    "score_prediction_accuracy",
]
