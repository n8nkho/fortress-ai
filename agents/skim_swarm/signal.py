"""Adaptive rule-based decisions — per-symbol params from symbol_learning."""
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
from agents.skim_swarm.symbol_learning import entry_blocked_by_causation, get_params
from utils.skim_swarm_config import (
    atr_k,
    max_spread_bps,
    max_stop_usd,
    mega_cap_tech_symbols,
    min_stop_usd,
    min_target_pct,
    min_target_usd,
    runtime_denylist,
    semi_symbols,
    stop_target_mult,
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


def stop_loss_usd(target: float) -> float:
    raw = max(target * stop_target_mult(), min_stop_usd())
    return -min(raw, max_stop_usd())


def _target_ok_for_volatility(features: dict[str, Any], target: float) -> bool:
    """Per-symbol: skip entries when profit target is unrealistic vs 1m ATR."""
    atr = _f(features.get("atr1m"))
    last = _f(features.get("last"))
    if atr <= 0 or last <= 0:
        return True
    atr_tgt = atr_k() * atr
    if target <= max(min_target_usd() * 2, last * min_target_pct() * 2):
        return True
    return atr_tgt >= target * 0.85


def _in_cooldown(st: dict[str, Any]) -> bool:
    raw = st.get("cooldown_until_utc")
    if not raw:
        return False
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < t
    except Exception:
        return False


def _short_blocked_by_symbol_spy_filter(params: dict[str, Any], spy_r5: float) -> bool:
    filt = float(params.get("short_spy_filter") or 0)
    return filt > 0 and spy_r5 > filt


def _try_entry(
    out: dict[str, Any],
    *,
    sym: str,
    features: dict[str, Any],
    score: float,
    pattern: str,
    side: str,
    action: str,
    reasoning: str,
    params: dict[str, Any],
    spy_r5: float,
) -> dict[str, Any] | None:
    """Apply per-symbol causation gate before returning an entry decision."""
    if pattern in (params.get("disable_patterns") or []):
        out["reasoning"] = f"pattern_disabled:{pattern}"
        return out
    if side == "long" and params.get("pause_long"):
        out["reasoning"] = "pause_long"
        return out
    if side == "short" and params.get("pause_short"):
        out["reasoning"] = "pause_short"
        return out
    if action == "enter_short" and _short_blocked_by_symbol_spy_filter(params, spy_r5):
        out["reasoning"] = f"symbol_short_spy_filter score={score:.2f}"
        return out
    blocked, reason = entry_blocked_by_causation(
        sym, pattern=pattern, side=side, features=features, score=score
    )
    if blocked:
        out["reasoning"] = reason or f"causation_blocked:{pattern}"
        return out
    out["action"] = action
    out["reasoning"] = reasoning
    return out


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

    # Optional manual denylist (env/file only) — not auto-applied
    if side == "flat" and sym in runtime_denylist():
        out["reasoning"] = "manual_denylist"
        return out

    params_early = get_params(sym)
    if side == "flat" and params_early.get("pause_entries"):
        out["reasoning"] = "pause_entries"
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

    if side == "long" and unreal is not None:
        u = _f(unreal)
        symbol_state["peak_unrealized"] = max(peak, u)
        peak = _f(symbol_state.get("peak_unrealized"))
        stop_usd = stop_loss_usd(target)
        if u >= target:
            out["action"] = "exit_position"
            out["reasoning"] = f"skim_target_hit:{u:.3f}>={target:.3f}"
            return out
        if u > 0 and peak >= target * 0.6 and u < peak * 0.55:
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
        stop_usd = stop_loss_usd(target)
        if u >= target:
            out["action"] = "exit_position"
            out["reasoning"] = f"skim_target_hit:{u:.3f}>={target:.3f}"
            return out
        if u > 0 and peak >= target * 0.6 and u < peak * 0.55:
            out["action"] = "exit_position"
            out["reasoning"] = "trailing_giveback"
            return out
        if u <= stop_usd:
            out["action"] = "exit_position"
            out["reasoning"] = f"stop_loss:{u:.3f}"
            return out
        out["reasoning"] = f"hold_short:{u:.3f}"
        return out

    if side != "flat":
        return out

    if open_positions >= max_open:
        out["reasoning"] = "max_open_positions"
        return out

    spread_limit = max_spread_bps() * (1.4 if sym in thin_etf_symbols() else 1.0)
    feat_spread = features.get("spread_bps")
    if feat_spread is not None and _f(feat_spread) > spread_limit:
        out["reasoning"] = f"spread_too_wide:{_f(feat_spread):.1f}>{spread_limit:.1f}"
        return out
    if last <= 0:
        out["reasoning"] = "no_price"
        return out

    if not _target_ok_for_volatility(features, target):
        out["reasoning"] = "target_unjustified_for_atr"
        return out

    params = get_params(sym)
    pd = params.get("pattern_deltas") or {}
    enter_long = float(params["enter_long"])
    enter_short = float(params["enter_short"])
    r1 = _f(features.get("r1m"))
    r5 = _f(features.get("r5m"))
    spy_r5 = _f(features.get("spy_r5m"))

    rip_thresh = enter_short + float(pd.get("rip_fade") or 0)
    if score <= rip_thresh and r5 < 0 and r1 > -0.0015:
        hit = _try_entry(
            out,
            sym=sym,
            features=features,
            score=score,
            pattern="rip_fade",
            side="short",
            action="enter_short",
            reasoning=f"rip_fade score={score:.2f}",
            params=params,
            spy_r5=spy_r5,
        )
        if hit is not None:
            return hit

    pb_thresh = enter_long + float(pd.get("pullback_uptrend") or 0)
    if score >= pb_thresh and r5 > 0 and r1 < 0.0015:
        hit = _try_entry(
            out,
            sym=sym,
            features=features,
            score=score,
            pattern="pullback_uptrend",
            side="long",
            action="enter_long",
            reasoning=f"pullback_uptrend score={score:.2f}",
            params=params,
            spy_r5=spy_r5,
        )
        if hit is not None:
            return hit

    ml_thresh = enter_long + 0.12 + float(pd.get("momentum_long") or 0)
    if score >= ml_thresh and r5 > 0.0008:
        hit = _try_entry(
            out,
            sym=sym,
            features=features,
            score=score,
            pattern="momentum_long",
            side="long",
            action="enter_long",
            reasoning=f"momentum_long score={score:.2f}",
            params=params,
            spy_r5=spy_r5,
        )
        if hit is not None:
            return hit

    ms_thresh = enter_short - 0.12 + float(pd.get("momentum_short") or 0)
    if score <= ms_thresh and r5 < -0.0008:
        hit = _try_entry(
            out,
            sym=sym,
            features=features,
            score=score,
            pattern="momentum_short",
            side="short",
            action="enter_short",
            reasoning=f"momentum_short score={score:.2f}",
            params=params,
            spy_r5=spy_r5,
        )
        if hit is not None:
            return hit

    out["reasoning"] = f"no_entry score={score:.2f}"
    return out
