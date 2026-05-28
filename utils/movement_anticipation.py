"""Symbol movement anticipation from hard 1m features — hypothesis-tagged pre-execution context."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _swarm_dir(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name


def research_state_path(component: str) -> Path:
    return _swarm_dir(component) / "research_state.json"


def movement_anticipation_enabled() -> bool:
    return str(os.environ.get("FORTRESS_MOVEMENT_ANTICIPATION", "1") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def movement_anticipation_gate_enabled() -> bool:
    return str(os.environ.get("FORTRESS_MOVEMENT_ANTICIPATION_GATE", "1") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def load_promoted_hypotheses(component: str) -> set[str]:
    p = research_state_path(component)
    if not p.exists():
        return set()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        promoted = doc.get("promoted") or []
        return {str(x) for x in promoted}
    except Exception:
        return set()


def _default_anticipation() -> dict[str, Any]:
    return {
        "enabled": False,
        "bias": 0.0,
        "regime": "neutral",
        "confidence": 0.0,
        "hypothesis_id": None,
        "score_delta": 0.0,
        "block_long": False,
        "block_short": False,
        "reasoning": "anticipation_off",
        "signals": {},
    }


def compute_movement_anticipation(
    features: dict[str, Any],
    *,
    component: str = "skim_swarm",
    promoted_hypotheses: set[str] | None = None,
) -> dict[str, Any]:
    """Derive directional anticipation from observable 1m returns — no synthetic prices."""
    if not movement_anticipation_enabled():
        return _default_anticipation()

    r1 = _f(features.get("r1m"))
    r3 = _f(features.get("r3m"))
    r5 = _f(features.get("r5m"))
    rsi = _f(features.get("rsi1m"), 50.0)
    spy_r5 = _f(features.get("spy_r5m"))
    if not spy_r5 and features.get("anchor_r5m") is not None:
        spy_r5 = _f(features.get("anchor_r5m"))
    residual = _f(features.get("residual_vs_spy"))
    if not residual and features.get("residual_vs_layer") is not None:
        residual = _f(features.get("residual_vs_layer"))
    elif not residual and features.get("residual_vs_anchor") is not None:
        residual = _f(features.get("residual_vs_anchor"))
    vix_raw = features.get("vix_last")
    vix = _f(vix_raw) if vix_raw is not None else None

    accel = r1 - (r3 / 3.0) if r3 else r1
    signals: dict[str, float | None] = {
        "r1m": r1,
        "r3m": r3,
        "r5m": r5,
        "accel": accel,
        "rsi1m": rsi,
        "residual_vs_spy": residual,
        "spy_r5m": spy_r5,
        "vix_last": vix,
    }

    regime = "neutral"
    bias = 0.0
    confidence = 0.0
    hypothesis_id: str | None = None
    parts: list[str] = []

    if abs(r5) > 0.0008 and r1 * r5 > 0 and abs(accel) > 0.00015:
        regime = "continuation"
        bias = 1.0 if r5 > 0 else -1.0
        confidence = min(1.0, abs(r5) * 480.0 + abs(accel) * 700.0)
        hypothesis_id = "momentum_continuation"
        parts.append(f"continuation r5={r5:.4f} accel={accel:.4f}")
    elif abs(r5) > 0.0010 and r1 * r5 < 0:
        regime = "mean_revert"
        bias = -1.0 if r5 > 0 else 1.0
        confidence = min(0.9, abs(r5) * 380.0 + abs(r1) * 200.0)
        hypothesis_id = "mean_revert_exhaustion"
        parts.append(f"mean_revert r5={r5:.4f} r1={r1:.4f}")
    elif abs(r5) < 0.00035 and abs(r1) < 0.00025 and 44.0 <= rsi <= 56.0:
        regime = "chop"
        bias = 0.0
        confidence = 0.62
        hypothesis_id = "chop_no_edge"
        parts.append("chop low impulse")

    if abs(residual) > 0.00055:
        rs_bias = 1.0 if residual > 0 else -1.0
        rs_conf = min(0.88, abs(residual) * 550.0)
        if rs_conf > confidence:
            regime = "relative_strength"
            bias = rs_bias
            confidence = rs_conf
            hypothesis_id = "spy_relative_strength"
            parts.append(f"residual_vs_spy={residual:.4f}")

    if vix is not None and vix > 28.0 and spy_r5 < -0.00045:
        regime = "risk_off"
        bias = min(bias, -0.35) if bias > 0 else bias - 0.25
        confidence = max(confidence, 0.72)
        hypothesis_id = "risk_off_vix_spy"
        parts.append(f"risk_off vix={vix:.1f} spy_r5={spy_r5:.4f}")

    score_delta = round(bias * confidence * 0.045, 4)
    promoted = promoted_hypotheses if promoted_hypotheses is not None else load_promoted_hypotheses(component)
    gate_on = movement_anticipation_gate_enabled()
    block_long = False
    block_short = False

    if gate_on and confidence >= 0.52:
        if regime == "continuation" and "block_countertrend_entries" in promoted:
            if bias > 0.35:
                block_short = True
            elif bias < -0.35:
                block_long = True
        if regime == "chop" and "chop_no_edge" in promoted:
            block_long = True
            block_short = True
        if hypothesis_id == "risk_off_vix_spy" and "risk_off_pause_longs" in promoted:
            block_long = True

    reasoning = "; ".join(parts) if parts else "neutral"
    return {
        "enabled": True,
        "bias": round(bias, 4),
        "regime": regime,
        "confidence": round(confidence, 4),
        "hypothesis_id": hypothesis_id,
        "score_delta": score_delta,
        "block_long": block_long,
        "block_short": block_short,
        "reasoning": reasoning,
        "signals": signals,
    }


def enrich_features_with_anticipation(
    features: dict[str, Any],
    *,
    component: str = "skim_swarm",
) -> dict[str, Any]:
    """Attach movement_anticipation block to features dict (in-place friendly)."""
    ant = compute_movement_anticipation(features, component=component)
    features["movement_anticipation"] = ant
    return features


def entry_blocked_by_anticipation(
    side: str,
    anticipation: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    """Pre-execution gate — only blocks when promoted hypothesis gates are active."""
    if not anticipation or not anticipation.get("enabled"):
        return False, None
    if side == "long" and anticipation.get("block_long"):
        hid = anticipation.get("hypothesis_id") or "anticipation"
        return True, f"anticipation_block_long:{hid}:{anticipation.get('regime')}"
    if side == "short" and anticipation.get("block_short"):
        hid = anticipation.get("hypothesis_id") or "anticipation"
        return True, f"anticipation_block_short:{hid}:{anticipation.get('regime')}"
    return False, None


def anticipation_score_adjustment(anticipation: dict[str, Any] | None) -> float:
    if not anticipation or not anticipation.get("enabled"):
        return 0.0
    return float(anticipation.get("score_delta") or 0.0)
