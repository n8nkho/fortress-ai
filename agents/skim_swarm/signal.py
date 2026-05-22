"""Adaptive rule-based decisions (long/short skim, no LLM)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.skim_swarm.eod import (
    describe_eod_phase,
    is_eod_caution_window,
    is_force_flatten_window,
    is_opening_blackout,
)
from agents.skim_swarm.company_context import context_score_adjustment
from agents.skim_swarm.symbol_learning import get_params
from utils.skim_swarm_config import (
    atr_k,
    max_spread_bps,
    mega_cap_tech_symbols,
    min_target_pct,
    min_target_usd,
    semi_symbols,
    thin_etf_symbols,
)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def adaptive_target_usd(features: dict[str, Any]) -> float:
    sym = str(features.get("symbol") or "")
    params = get_params(sym)
    last = _f(features.get("last"), 0)
    if last <= 0:
        return min_target_usd()
    atr = _f(features.get("atr1m"), 0)
    pct_tgt = last * min_target_pct()
    atr_tgt = atr_k() * atr if atr > 0 else 0
    base = max(min_target_usd(), pct_tgt, atr_tgt)
    base *= float(params.get("target_mult") or 1.0)
    if features.get("thin_etf"):
        base *= 1.35
    vix = features.get("vix_last")
    if vix is not None and _f(vix) > 28:
        base *= 1.15
    beta = features.get("company_beta")
    if beta is not None and _f(beta) > 1.25:
        base *= 1.08
    return round(base, 4)


def compute_score(features: dict[str, Any]) -> float:
    """Directional bias in [-1, 1]."""
    sym = str(features.get("symbol") or "")
    params = get_params(sym)
    r1 = _f(features.get("r1m"))
    r5 = _f(features.get("r5m"))
    res = _f(features.get("residual_vs_spy"))
    semi = _f(features.get("semi_lead_vs_soxx"))
    rsi = _f(features.get("rsi1m"), 50)
    rsi_norm = (rsi - 50) / 50.0
    score = 0.45 * (r5 * 200) + 0.25 * (r1 * 400) + 0.2 * (res * 150) + 0.1 * rsi_norm
    if sym in semi_symbols():
        score += 0.08 * (semi * 120)
    if sym in mega_cap_tech_symbols():
        score += 0.05 * (res * 100)
    ctx = features.get("company_context") if isinstance(features.get("company_context"), dict) else {}
    score += context_score_adjustment(ctx, features)
    score += float(params.get("score_bias") or 0)
    return max(-1.0, min(1.0, score))


def _in_cooldown(st: dict[str, Any]) -> bool:
    raw = st.get("cooldown_until_utc")
    if not raw:
        return False
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < t
    except Exception:
        return False


def decide(
    features: dict[str, Any],
    symbol_state: dict[str, Any],
    *,
    swarm_halted: bool,
    open_positions: int,
    max_open: int,
) -> dict[str, Any]:
    sym = str(features.get("symbol") or "")
    side = str(features.get("side") or symbol_state.get("side") or "flat")
    score = compute_score(features)
    target = adaptive_target_usd(features)
    phase = describe_eod_phase()
    last = _f(features.get("last"))

    out: dict[str, Any] = {
        "symbol": sym,
        "action": "wait",
        "score": round(score, 4),
        "target_usd": target,
        "eod_phase": phase,
        "reasoning": "no_edge",
        "confidence": abs(score),
    }

    if swarm_halted:
        out["reasoning"] = "swarm_halted"
        return out

    if is_force_flatten_window() or phase == "force_flatten":
        if side != "flat":
            out["action"] = "flatten"
            out["reasoning"] = "eod_force_flatten"
        return out

    if side == "flat" and _in_cooldown(symbol_state):
        out["reasoning"] = "cooldown"
        return out

    if is_opening_blackout() and side == "flat":
        out["reasoning"] = "opening_blackout"
        return out

    if is_eod_caution_window() and side == "flat":
        out["reasoning"] = "eod_caution_no_new_entries"
        return out

    unreal = features.get("unrealized_usd")
    peak = _f(symbol_state.get("peak_unrealized"), 0)

    # --- in position: skim exit ---
    if side == "long" and unreal is not None:
        u = _f(unreal)
        symbol_state["peak_unrealized"] = max(peak, u)
        peak = _f(symbol_state.get("peak_unrealized"))
        stop_usd = -max(target * 1.5, 0.25)
        if u >= target:
            out["action"] = "exit_position"
            out["reasoning"] = f"skim_target_hit:{u:.3f}>={target:.3f}"
            return out
        if peak >= target * 0.6 and u < peak * 0.55:
            out["action"] = "exit_position"
            out["reasoning"] = "trailing_giveback"
            return out
        if u <= stop_usd:
            out["action"] = "exit_position"
            out["reasoning"] = f"stop_loss:{u:.3f}"
            return out
        out["reasoning"] = f"hold_long:{u:.3f}"
        return out

    if side == "short" and unreal is not None:
        u = _f(unreal)
        symbol_state["peak_unrealized"] = max(peak, u)
        peak = _f(symbol_state.get("peak_unrealized"))
        stop_usd = -max(target * 1.5, 0.25)
        if u >= target:
            out["action"] = "exit_position"
            out["reasoning"] = f"skim_target_hit:{u:.3f}>={target:.3f}"
            return out
        if peak >= target * 0.6 and u < peak * 0.55:
            out["action"] = "exit_position"
            out["reasoning"] = "trailing_giveback"
            return out
        if u <= stop_usd:
            out["action"] = "exit_position"
            out["reasoning"] = f"stop_loss:{u:.3f}"
            return out
        out["reasoning"] = f"hold_short:{u:.3f}"
        return out

    # --- flat: entries ---
    if side != "flat":
        return out

    if open_positions >= max_open:
        out["reasoning"] = "max_open_positions"
        return out

    spread_bps = max_spread_bps() * (1.4 if sym in thin_etf_symbols() else 1.0)
    if last <= 0:
        out["reasoning"] = "no_price"
        return out

    params = get_params(sym)
    enter_long = float(params["enter_long"])
    enter_short = float(params["enter_short"])

    r1 = _f(features.get("r1m"))
    r5 = _f(features.get("r5m"))

    if score >= enter_long and r5 > 0 and r1 < 0.0015:
        out["action"] = "enter_long"
        out["reasoning"] = f"pullback_uptrend score={score:.2f}"
        return out

    if score <= enter_short and r5 < 0 and r1 > -0.0015:
        out["action"] = "enter_short"
        out["reasoning"] = f"rip_fade score={score:.2f}"
        return out

    if score >= enter_long + 0.12 and r5 > 0.0008:
        out["action"] = "enter_long"
        out["reasoning"] = f"momentum_long score={score:.2f}"
        return out

    if score <= enter_short - 0.12 and r5 < -0.0008:
        out["action"] = "enter_short"
        out["reasoning"] = f"momentum_short score={score:.2f}"
        return out

    out["reasoning"] = f"no_entry score={score:.2f}"
    return out
