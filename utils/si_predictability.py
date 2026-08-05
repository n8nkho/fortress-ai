"""SI predictability — evolvable prediction model + forecast scoring.

Built-in learnable model:
- Per-action-family priors (delta, confidence) update online from resolved outcomes
- Feature weights (rolling exp/pay, alpha, tape) drift via low-rate gradient-style EMA
- Scale advice: SI intervention strength multiplies by model reliability (bounded)

Never loosens pre_trade_gate or immutable caps. Predictions are inspectable in
data/si_capability/predictions.jsonl and model in data/si_capability/prediction_model.json.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from utils.system_time import now, now_iso

log = logging.getLogger(__name__)

_MARKER = "si_predictability"
_MODEL_VERSION = 2

# Prior catalog — seed for families; learned values override after resolve cycles.
_FAMILY_PRIORS: dict[str, dict[str, float]] = {
    "symbol_session_brake": {"prior_delta": 0.0, "prior_conf": 0.72, "protective": 1.0},
    "swarm_session_tight": {"prior_delta": 0.0, "prior_conf": 0.65, "protective": 1.0},
    "swarm_session_churn": {"prior_delta": 0.0, "prior_conf": 0.6, "protective": 1.0},
    "edge_autofix": {"prior_delta": 0.005, "prior_conf": 0.45, "protective": 0.0},
    "deep_lag_wait": {"prior_delta": 0.0, "prior_conf": 0.7, "protective": 1.0},
    "infra_strong_tape_soft": {"prior_delta": 0.003, "prior_conf": 0.4, "protective": 0.0},
    "denylist_thaw": {"prior_delta": 0.002, "prior_conf": 0.4, "protective": 0.0},
    "denylist_audit": {"prior_delta": 0.0, "prior_conf": 0.55, "protective": 1.0},
    "constructive_tape_override": {"prior_delta": 0.0, "prior_conf": 0.55, "protective": 1.0},
    "gap_dispatch": {"prior_delta": 0.0, "prior_conf": 0.6, "protective": 1.0},
    "first_session_loss": {"prior_delta": 0.0, "prior_conf": 0.7, "protective": 1.0},
    "default": {"prior_delta": 0.0, "prior_conf": 0.4, "protective": 0.0},
}

_FEATURE_KEYS = (
    "rolling_exp",
    "rolling_pay",
    "alpha_vs_spy",
    "strong_tape",
    "session_exits_norm",
    "open_positions_norm",
)


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def predictability_log_path() -> Path:
    return _data_dir() / "si_capability" / "predictions.jsonl"


def prediction_model_path() -> Path:
    return _data_dir() / "si_capability" / "prediction_model.json"


def predictability_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_PREDICTABILITY", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def evolution_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_PREDICTION_EVOLVE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def action_family(action: str) -> str:
    a = str(action or "").strip()
    if a.startswith("gap_dispatch:"):
        inner = a.split(":", 1)[-1]
        if inner in _FAMILY_PRIORS:
            return inner
        return "gap_dispatch"
    if a in _FAMILY_PRIORS:
        return a
    if "brake" in a or "pause" in a or "first_session_loss" in a:
        return "symbol_session_brake"
    if "tight" in a:
        return "swarm_session_tight"
    if "soft" in a:
        return "infra_strong_tape_soft"
    if "deep_lag" in a:
        return "deep_lag_wait"
    return "default"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _default_model() -> dict[str, Any]:
    families: dict[str, Any] = {}
    for fam, prior in _FAMILY_PRIORS.items():
        families[fam] = {
            "prior_delta": prior["prior_delta"],
            "prior_conf": prior["prior_conf"],
            "protective": prior["protective"],
            "learned_delta": prior["prior_delta"],
            "learned_conf": prior["prior_conf"],
            "n": 0,
            "hits": 0,
            "hit_rate": 0.5,
            "avg_abs_error": 0.0,
            "brier_ema": 0.25,
        }
    return {
        "version": _MODEL_VERSION,
        "marker": _MARKER,
        "updated_utc": now_iso(),
        "global": {
            "lr": float(os.environ.get("FORTRESS_SI_PRED_LR", "0.08") or 0.08),
            "n_updates": 0,
            "accuracy_ema": 0.55,
            "brier_ema": 0.25,
            "resolved_total": 0,
        },
        "action_families": families,
        "feature_weights": {k: 0.0 for k in _FEATURE_KEYS},
        "scale": {
            "strength_mult": 1.0,
            "participation_mult": 1.0,
            "soft_path_mult": 1.0,
            "min_updates_for_scale": 8,
        },
        "resolved_ids": [],
    }


def load_prediction_model() -> dict[str, Any]:
    p = prediction_model_path()
    if not p.is_file():
        return _default_model()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return _default_model()
        base = _default_model()
        # Merge families so new families appear after upgrades.
        fam = dict(base["action_families"])
        for k, v in (doc.get("action_families") or {}).items():
            if isinstance(v, dict):
                merged = dict(fam.get(k) or base["action_families"]["default"])
                merged.update(v)
                fam[k] = merged
        base.update({k: v for k, v in doc.items() if k != "action_families"})
        base["action_families"] = fam
        base["version"] = _MODEL_VERSION
        fw = dict(base["feature_weights"])
        for k, v in (doc.get("feature_weights") or {}).items():
            if k in fw:
                try:
                    fw[k] = float(v)
                except (TypeError, ValueError):
                    pass
        base["feature_weights"] = fw
        return base
    except Exception:
        return _default_model()


def save_prediction_model(doc: dict[str, Any]) -> None:
    p = prediction_model_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(doc)
    doc["updated_utc"] = now_iso()
    doc["marker"] = _MARKER
    doc["version"] = _MODEL_VERSION
    # Cap resolved id ring
    ids = list(doc.get("resolved_ids") or [])
    if len(ids) > 800:
        doc["resolved_ids"] = ids[-800:]
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def extract_features(
    *,
    component: str,
    metrics: dict[str, Any] | None,
    detail: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Bounded numeric features for the evolvable model (fixed domain)."""
    metrics = metrics or {}
    detail = detail or {}
    comp = metrics.get(component) or {}
    port = metrics.get("portfolio_session") or {}

    def _f(src: dict[str, Any], key: str, default: float = 0.0) -> float:
        try:
            v = src.get(key)
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    exp = comp.get("rolling_expectancy_usd")
    if exp is None:
        exp = comp.get("session_expectancy_usd")
    try:
        exp_f = float(exp) if exp is not None else 0.0
    except (TypeError, ValueError):
        exp_f = 0.0
    pay = _f(comp, "rolling_payoff_ratio", 1.0)
    alpha = _f(port, "alpha_vs_spy_pct", _f(detail, "alpha_vs_spy_pct", 0.0))
    strong = 1.0 if port.get("strong_tape_1d") or detail.get("strong_tape_1d") else 0.0
    exits = _f(port, "session_exit_count", _f(comp, "session_exits", 0.0))
    open_n = _f(comp, "open_positions", _f(port, "open_positions", 0.0))

    # Normalize into roughly [-1, 1] / [0, 1] ranges so weight updates stay stable.
    return {
        "rolling_exp": _clamp(exp_f / 0.5, -1.0, 1.0),
        "rolling_pay": _clamp((pay - 1.0) / 1.0, -1.0, 1.0),
        "alpha_vs_spy": _clamp(alpha / 2.0, -1.0, 1.0),
        "strong_tape": strong,
        "session_exits_norm": _clamp(exits / 10.0, 0.0, 1.0),
        "open_positions_norm": _clamp(open_n / 8.0, 0.0, 1.0),
    }


def _family_state(model: dict[str, Any], family: str) -> dict[str, Any]:
    fams = model.setdefault("action_families", {})
    if family not in fams:
        prior = _FAMILY_PRIORS.get(family) or _FAMILY_PRIORS["default"]
        fams[family] = {
            "prior_delta": prior["prior_delta"],
            "prior_conf": prior["prior_conf"],
            "protective": prior["protective"],
            "learned_delta": prior["prior_delta"],
            "learned_conf": prior["prior_conf"],
            "n": 0,
            "hits": 0,
            "hit_rate": 0.5,
            "avg_abs_error": 0.0,
            "brier_ema": 0.25,
        }
    return fams[family]


def predict_intervention_outcome(
    *,
    component: str,
    action: str,
    metrics: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Forecast expectancy delta using evolved model + feature blend."""
    metrics = metrics or {}
    detail = detail or {}
    family = action_family(action)
    model = load_prediction_model()
    st = _family_state(model, family)
    features = extract_features(component=component, metrics=metrics, detail=detail)
    weights = model.get("feature_weights") or {}

    # Feature contribution to delta (cents-scale) — bounded.
    feat_delta = 0.0
    for k in _FEATURE_KEYS:
        try:
            feat_delta += float(weights.get(k) or 0.0) * float(features.get(k) or 0.0)
        except (TypeError, ValueError):
            pass
    feat_delta = _clamp(feat_delta, -0.03, 0.03)

    learned_delta = float(st.get("learned_delta") if st.get("learned_delta") is not None else st.get("prior_delta") or 0)
    predicted_delta = _clamp(learned_delta + feat_delta, -0.05, 0.05)

    # Confidence: learned conf × reliability (hit_rate) × global accuracy_ema
    base_conf = float(st.get("learned_conf") if st.get("learned_conf") is not None else st.get("prior_conf") or 0.4)
    hit_rate = float(st.get("hit_rate") if st.get("hit_rate") is not None else 0.5)
    g_acc = float((model.get("global") or {}).get("accuracy_ema") or 0.55)
    n = int(st.get("n") or 0)
    # Shrinkage toward prior when few samples.
    conf = base_conf * (0.55 + 0.45 * hit_rate) * (0.7 + 0.3 * g_acc)
    if n < 3:
        conf = 0.55 * conf + 0.45 * float(st.get("prior_conf") or 0.4)
    conf = _clamp(conf, 0.15, 0.92)

    protective = float(st.get("protective") or 0) >= 0.5
    if protective or predicted_delta <= 0.0005:
        outcome = "hold_or_improve_expectancy"
    elif predicted_delta > 0.004:
        outcome = "slight_expectancy_lift"
    else:
        outcome = "maintain_baseline"

    comp_m = metrics.get(component) or {}
    exp = comp_m.get("rolling_expectancy_usd")
    if exp is None:
        exp = comp_m.get("session_expectancy_usd")
    try:
        exp_f = float(exp) if exp is not None else 0.0
    except (TypeError, ValueError):
        exp_f = 0.0

    pred_id = str(uuid.uuid4())
    try:
        horizon = max(15, int(os.environ.get("FORTRESS_SI_PRED_HORIZON_MIN", "60") or 60))
    except ValueError:
        horizon = 60

    return {
        "id": pred_id,
        "component": component,
        "action": action,
        "action_family": family,
        "predicted_delta_expectancy_usd": round(predicted_delta, 4),
        "predicted_outcome": outcome,
        "confidence": round(conf, 4),
        "baseline_expectancy_usd": round(exp_f, 4),
        "features": {k: round(float(features[k]), 4) for k in features},
        "model_version": _MODEL_VERSION,
        "model_n_updates": int((model.get("global") or {}).get("n_updates") or 0),
        "family_n": n,
        "family_hit_rate": round(hit_rate, 4),
        "horizon_minutes": horizon,
        "resolved": False,
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


def _read_tail(path: Path, *, max_bytes: int = 400_000) -> list[dict[str, Any]]:
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


def _expectancy_after(metrics: dict[str, Any], component: str) -> float | None:
    comp = metrics.get(component) or {}
    for key in ("rolling_expectancy_usd", "session_expectancy_usd"):
        val = comp.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _fingerprint(row: dict[str, Any]) -> str:
    if row.get("id"):
        return str(row["id"])
    raw = f"{row.get('ts')}|{row.get('component')}|{row.get('action')}|{row.get('baseline_expectancy_usd')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _update_family_from_outcome(
    model: dict[str, Any],
    *,
    family: str,
    pred_delta: float,
    actual_delta: float,
    hit: bool,
    features: dict[str, float] | None,
    confidence: float,
) -> None:
    st = _family_state(model, family)
    g = model.setdefault("global", {})
    try:
        lr = float(g.get("lr") or 0.08)
    except (TypeError, ValueError):
        lr = 0.08
    lr = _clamp(lr, 0.01, 0.25)

    error = actual_delta - pred_delta
    # Move learned_delta toward observed outcome (online mean-ish correction).
    learned = float(st.get("learned_delta") or 0.0)
    st["learned_delta"] = round(_clamp(learned + lr * error, -0.04, 0.04), 5)

    n = int(st.get("n") or 0) + 1
    hits = int(st.get("hits") or 0) + (1 if hit else 0)
    st["n"] = n
    st["hits"] = hits
    st["hit_rate"] = round(hits / n, 4)

    abs_err = abs(error)
    prev_err = float(st.get("avg_abs_error") or 0.0)
    st["avg_abs_error"] = round((prev_err * (n - 1) + abs_err) / n, 5)

    # Confidence calibration: raise slightly on hit, lower on miss.
    conf = float(st.get("learned_conf") if st.get("learned_conf") is not None else st.get("prior_conf") or 0.4)
    st["learned_conf"] = round(_clamp(conf + (0.03 if hit else -0.04), 0.2, 0.9), 4)

    # Brier-like: (conf - hit)^2
    brier = (float(confidence) - (1.0 if hit else 0.0)) ** 2
    st["brier_ema"] = round(0.85 * float(st.get("brier_ema") or 0.25) + 0.15 * brier, 5)

    # Feature weight evolution: small step if feature co-occurred with positive residual.
    if features:
        fw = model.setdefault("feature_weights", {})
        for k, fv in features.items():
            if k not in _FEATURE_KEYS:
                continue
            try:
                fvf = float(fv)
            except (TypeError, ValueError):
                continue
            # Only nudge when |feature| is material to avoid noise.
            if abs(fvf) < 0.05:
                continue
            w = float(fw.get(k) or 0.0)
            # Gradient of squared error vs linear feat term: -error * feature
            step = lr * 0.15 * error * fvf
            fw[k] = round(_clamp(w + step, -0.05, 0.05), 5)

    g["n_updates"] = int(g.get("n_updates") or 0) + 1
    g["resolved_total"] = int(g.get("resolved_total") or 0) + 1
    acc_ema = float(g.get("accuracy_ema") or 0.55)
    g["accuracy_ema"] = round(0.9 * acc_ema + 0.1 * (1.0 if hit else 0.0), 4)
    g["brier_ema"] = round(0.9 * float(g.get("brier_ema") or 0.25) + 0.1 * brier, 5)


def _recompute_scale(model: dict[str, Any]) -> dict[str, Any]:
    """Translate model reliability into SI scale multipliers (bounded)."""
    g = model.get("global") or {}
    acc = float(g.get("accuracy_ema") or 0.55)
    n = int(g.get("n_updates") or 0)
    scale = model.setdefault("scale", {})
    min_u = int(scale.get("min_updates_for_scale") or 8)
    if n < min_u:
        scale["strength_mult"] = 1.0
        scale["participation_mult"] = 1.0
        scale["soft_path_mult"] = 1.0
        scale["mode"] = "warmup"
        return scale

    # Accuracy 0.35 → 0.75 mult, 0.55 → 1.0, 0.80 → 1.25
    strength = _clamp(0.75 + (acc - 0.55) * 1.2, 0.7, 1.35)
    soft_fam = _family_state(model, "infra_strong_tape_soft")
    soft_hr = float(soft_fam.get("hit_rate") or 0.5)
    soft_n = int(soft_fam.get("n") or 0)
    soft_mult = 1.0
    if soft_n >= 3:
        soft_mult = _clamp(0.6 + soft_hr * 0.7, 0.5, 1.2)
    brake_fam = _family_state(model, "symbol_session_brake")
    part = _clamp(0.85 + float(brake_fam.get("hit_rate") or 0.5) * 0.3, 0.8, 1.2)

    scale["strength_mult"] = round(strength, 4)
    scale["soft_path_mult"] = round(soft_mult, 4)
    scale["participation_mult"] = round(part, 4)
    scale["mode"] = "scaled"
    return scale


def evolve_prediction_model(
    metrics: dict[str, Any],
    *,
    lookback: int = 48,
    max_updates: int = 24,
) -> dict[str, Any]:
    """Resolve aged predictions against live metrics and update the learning model."""
    if not predictability_enabled() or not evolution_enabled():
        return {"skipped": "predictability_or_evolve_disabled", "marker": _MARKER}

    model = load_prediction_model()
    resolved_ids = set(str(x) for x in (model.get("resolved_ids") or []))
    rows = _read_tail(predictability_log_path())[-lookback:]
    updates = 0
    hits = 0
    scored = 0

    # Process oldest first among unresolved so evolution is chronological.
    for row in rows:
        if updates >= max_updates:
            break
        if row.get("scoreable") is False:
            continue
        if row.get("resolved") is True:
            continue
        fp = _fingerprint(row)
        if fp in resolved_ids:
            continue
        comp = str(row.get("component") or "")
        if not comp or comp == "si_meta":
            continue
        baseline = row.get("baseline_expectancy_usd")
        if baseline is None:
            continue
        after = _expectancy_after(metrics, comp)
        if after is None:
            continue
        # Require prediction age ≥ half horizon (or any age if re-scored with force lookback).
        try:
            horizon = int(row.get("horizon_minutes") or 60)
        except (TypeError, ValueError):
            horizon = 60
        ts_raw = str(row.get("ts") or "")
        age_ok = True
        if ts_raw:
            try:
                from utils.system_time import parse_iso

                ts = parse_iso(ts_raw)
                if ts is not None:
                    age_min = (now() - ts).total_seconds() / 60.0
                    # Allow resolve after half horizon to tighten learning feedback.
                    age_ok = age_min >= max(10.0, horizon * 0.5)
            except Exception:
                age_ok = True
        if not age_ok:
            continue

        # Legacy heuristic predictions (pre model_version 2) lack feature vectors and
        # reliable horizon — mark seen without training to avoid poisoning learned priors.
        try:
            mv = int(row.get("model_version") or 0)
        except (TypeError, ValueError):
            mv = 0
        if mv < 2 and not isinstance(row.get("features"), dict):
            resolved_ids.add(fp)
            continue

        try:
            baseline_f = float(baseline)
            after_f = float(after)
            pred_d = float(row.get("predicted_delta_expectancy_usd") or 0)
            conf = float(row.get("confidence") or 0.5)
        except (TypeError, ValueError):
            continue

        # Only resolve predictions from this/previous session — older rows
        # resolve against unrelated live metrics and poison the model.
        sess = str(row.get("session_date_et") or "")[:10]
        today = now().date().isoformat()
        if sess and sess < today:
            from datetime import date

            try:
                d0 = date.fromisoformat(sess)
                if (now().date() - d0).days > 1:
                    resolved_ids.add(fp)  # drop stale without learning
                    continue
            except ValueError:
                pass

        actual_delta = after_f - baseline_f
        # Unrealistically large expected drift for a 60m horizon → skip (metric noise).
        if abs(actual_delta) > 0.5:
            resolved_ids.add(fp)
            continue

        family = str(row.get("action_family") or action_family(str(row.get("action") or "")))
        st_pre = _family_state(model, family)
        protective = float(st_pre.get("protective") or 0) >= 0.5
        # Protective: hit if held (not worse than 1¢); growth actions: hit if >= predicted delta.
        if protective:
            hit = actual_delta >= -0.01
        else:
            hit = actual_delta >= pred_d - 0.01
        feats = row.get("features") if isinstance(row.get("features"), dict) else None
        features_f: dict[str, float] | None = None
        if feats:
            features_f = {}
            for k, v in feats.items():
                try:
                    features_f[str(k)] = float(v)
                except (TypeError, ValueError):
                    pass

        _update_family_from_outcome(
            model,
            family=family,
            pred_delta=pred_d,
            actual_delta=actual_delta,
            hit=hit,
            features=features_f,
            confidence=conf,
        )
        resolved_ids.add(fp)
        updates += 1
        scored += 1
        if hit:
            hits += 1

    if updates:
        model["resolved_ids"] = list(resolved_ids)[-800:]
        _recompute_scale(model)
        save_prediction_model(model)
        # Append resolution summary for audit (one compact row).
        try:
            record_prediction(
                {
                    "ts": now_iso(),
                    "marker": "si_prediction_evolution",
                    "type": "evolution_batch",
                    "updates": updates,
                    "hits": hits,
                    "accuracy_batch": round(hits / scored, 4) if scored else None,
                    "accuracy_ema": (model.get("global") or {}).get("accuracy_ema"),
                    "scale": model.get("scale"),
                    "scoreable": False,
                    "resolved": True,
                    "session_date_et": now().date().isoformat(),
                }
            )
        except Exception:
            pass
        log.info(
            "si_prediction_evolution updates=%s hits=%s acc_ema=%s scale=%s",
            updates,
            hits,
            (model.get("global") or {}).get("accuracy_ema"),
            (model.get("scale") or {}).get("strength_mult"),
        )

    return {
        "ok": True,
        "marker": _MARKER,
        "updates": updates,
        "hits": hits,
        "scored": scored,
        "accuracy_ema": (model.get("global") or {}).get("accuracy_ema"),
        "n_updates": (model.get("global") or {}).get("n_updates"),
        "scale": model.get("scale"),
        "top_families": _top_families_snapshot(model),
    }


def _top_families_snapshot(model: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    fams = model.get("action_families") or {}
    rows = []
    for name, st in fams.items():
        if name == "default":
            continue
        n = int(st.get("n") or 0)
        if n <= 0:
            continue
        rows.append(
            {
                "family": name,
                "n": n,
                "hit_rate": st.get("hit_rate"),
                "learned_delta": st.get("learned_delta"),
                "learned_conf": st.get("learned_conf"),
            }
        )
    rows.sort(key=lambda x: int(x.get("n") or 0), reverse=True)
    return rows[:limit]


def score_prediction_accuracy(
    metrics: dict[str, Any],
    *,
    lookback: int = 24,
    evolve: bool = True,
) -> dict[str, Any]:
    """Fraction of recent predictions whose expectancy outcome matched (held/improved).

    When evolve=True, also runs online model updates for aged unresolved forecasts.
    """
    evo: dict[str, Any] = {}
    if evolve and evolution_enabled():
        try:
            evo = evolve_prediction_model(metrics, lookback=max(lookback, 48))
        except Exception as e:
            evo = {"error": str(e)[:120]}

    rows = _read_tail(predictability_log_path())[-lookback:]
    # Skip evolution/audit rows.
    rows = [r for r in rows if r.get("type") != "evolution_batch" and r.get("scoreable") is not False]
    if not rows:
        return {
            "accuracy": None,
            "scored": 0,
            "marker": _MARKER,
            "note": "no_predictions",
            "evolution": evo,
            "model": model_status_summary(),
        }

    scored = 0
    hits = 0
    for row in rows:
        comp = str(row.get("component") or "")
        if not comp or comp == "si_meta":
            continue
        baseline = row.get("baseline_expectancy_usd")
        pred_delta = row.get("predicted_delta_expectancy_usd")
        if baseline is None:
            continue
        after = _expectancy_after(metrics, comp)
        if after is None:
            continue
        try:
            baseline_f = float(baseline)
            after_f = float(after)
            pred_d = float(pred_delta or 0)
        except (TypeError, ValueError):
            continue
        scored += 1
        actual_delta = after_f - baseline_f
        if actual_delta >= pred_d - 0.01:
            hits += 1

    model = load_prediction_model()
    return {
        "accuracy": round(hits / scored, 4) if scored else None,
        "scored": scored,
        "hits": hits,
        "marker": _MARKER,
        "target_min": 0.55,
        "evolution": evo,
        "accuracy_ema": (model.get("global") or {}).get("accuracy_ema"),
        "scale": model.get("scale"),
        "model": model_status_summary(model),
    }


def model_status_summary(model: dict[str, Any] | None = None) -> dict[str, Any]:
    model = model if model is not None else load_prediction_model()
    g = model.get("global") or {}
    return {
        "version": model.get("version"),
        "n_updates": g.get("n_updates"),
        "accuracy_ema": g.get("accuracy_ema"),
        "brier_ema": g.get("brier_ema"),
        "scale": model.get("scale"),
        "feature_weights": model.get("feature_weights"),
        "top_families": _top_families_snapshot(model),
        "marker": _MARKER,
    }


def prediction_scale_multipliers() -> dict[str, float]:
    """Bounded scale factors for other SI subsystems (strength / soft / participation)."""
    if not predictability_enabled():
        return {
            "strength_mult": 1.0,
            "soft_path_mult": 1.0,
            "participation_mult": 1.0,
            "mode": "disabled",
            "n_updates": 0,
            "accuracy_ema": 0.55,
        }
    model = load_prediction_model()
    scale = _recompute_scale(model)
    return {
        "strength_mult": float(scale.get("strength_mult") or 1.0),
        "soft_path_mult": float(scale.get("soft_path_mult") or 1.0),
        "participation_mult": float(scale.get("participation_mult") or 1.0),
        "mode": str(scale.get("mode") or "warmup"),
        "n_updates": int((model.get("global") or {}).get("n_updates") or 0),
        "accuracy_ema": float((model.get("global") or {}).get("accuracy_ema") or 0.55),
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
    "action_family",
    "attach_prediction_to_intervention",
    "evolve_prediction_model",
    "extract_features",
    "load_prediction_model",
    "model_status_summary",
    "predict_intervention_outcome",
    "predictability_enabled",
    "prediction_scale_multipliers",
    "record_prediction",
    "score_prediction_accuracy",
]
