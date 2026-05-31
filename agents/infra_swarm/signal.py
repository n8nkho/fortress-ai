"""Stack Residual Propagation (SRP) decisions — per-symbol infra params."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.infra_swarm.company_context import context_score_adjustment
from agents.infra_swarm.eod import (
    describe_eod_phase,
    is_eod_caution_window,
    is_force_flatten_window,
    is_opening_blackout,
)
from agents.infra_swarm.symbol_learning import entry_blocked_by_causation, get_params
from utils.movement_anticipation import (
    anticipation_score_adjustment,
    entry_blocked_by_anticipation,
)
from utils.edge_quality import evaluate_entry_edge_gates, time_stop_triggered
from utils.infra_swarm_config import (
    anchor_symbol,
    atr_k,
    high_vol_symbols,
    high_vol_target_cap_usd,
    layer_for_symbol,
    lead_impulse_threshold,
    max_spread_bps,
    max_stop_usd,
    min_stop_usd,
    min_target_pct,
    min_target_usd,
    propagation_lag_tolerance,
    runtime_denylist,
    stop_target_mult,
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
    base *= float(params.get("target_mult_effective") or params.get("target_mult") or 1.0)
    if sym in high_vol_symbols():
        base = min(base, high_vol_target_cap_usd())
    vix = features.get("vix_last")
    if vix is not None and _f(vix) > 28:
        base *= 1.12
    beta = features.get("company_beta")
    if beta is not None and _f(beta) > 1.25:
        base *= 1.06
    return round(base, 4)


def compute_score(features: dict[str, Any]) -> float:
    sym = str(features.get("symbol") or "")
    params = get_params(sym)
    r1 = _f(features.get("r1m"))
    r5 = _f(features.get("r5m"))
    res_layer = _f(features.get("residual_vs_layer"))
    lag = _f(features.get("propagation_lag_vs_l1"))
    rsi = _f(features.get("rsi1m"), 50)
    rsi_norm = (rsi - 50) / 50.0
    score = 0.35 * (r5 * 200) + 0.20 * (r1 * 400) + 0.25 * (res_layer * 180)
    layer = layer_for_symbol(sym)
    if layer != "L1":
        score += 0.15 * (lag * 220)
    else:
        score += 0.10 * (r5 * 150)
    score += 0.05 * rsi_norm
    breadth = _f(features.get("infra_breadth"), 0.5)
    score += (breadth - 0.5) * 0.08
    ctx = features.get("company_context") if isinstance(features.get("company_context"), dict) else {}
    score += context_score_adjustment(ctx, features)
    score += float(params.get("score_bias") or 0)
    ant = features.get("movement_anticipation")
    score += anticipation_score_adjustment(ant if isinstance(ant, dict) else None)
    return max(-1.0, min(1.0, score))


def stop_loss_usd(target: float, *, stop_target_mult_effective: float | None = None) -> float:
    mult = stop_target_mult_effective if stop_target_mult_effective is not None else stop_target_mult()
    raw = max(target * mult, min_stop_usd())
    return -min(raw, max_stop_usd())


def _in_cooldown(st: dict[str, Any]) -> bool:
    raw = st.get("cooldown_until_utc")
    if not raw:
        return False
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < t
    except Exception:
        return False


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
    target_usd: float,
    stop_usd: float,
) -> dict[str, Any] | None:
    if pattern in (params.get("disable_patterns") or []):
        out["reasoning"] = f"pattern_disabled:{pattern}"
        return out
    if side == "long" and params.get("pause_long"):
        out["reasoning"] = "pause_long"
        return out
    if side == "short" and params.get("pause_short"):
        out["reasoning"] = "pause_short"
        return out
    ant = features.get("movement_anticipation")
    blocked_ant, ant_reason = entry_blocked_by_anticipation(
        side, ant if isinstance(ant, dict) else None
    )
    if blocked_ant:
        out["reasoning"] = ant_reason or "anticipation_blocked"
        return out
    blocked, reason = entry_blocked_by_causation(
        sym, pattern=pattern, side=side, features=features, score=score
    )
    if blocked:
        out["reasoning"] = reason or f"causation_blocked:{pattern}"
        return out
    blocked_edge, edge_reason, edge_meta = evaluate_entry_edge_gates(
        symbol=sym,
        pattern=pattern,
        side=side,
        features=features,
        target_usd=target_usd,
        stop_usd=stop_usd,
        component="infra_swarm",
    )
    if blocked_edge:
        out["reasoning"] = edge_reason or "edge_gate_blocked"
        out["edge_gate"] = edge_meta
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
    ant = features.get("movement_anticipation") if isinstance(features.get("movement_anticipation"), dict) else None
    phase = describe_eod_phase()
    last = _f(features.get("last"))
    layer = layer_for_symbol(sym)

    out: dict[str, Any] = {
        "symbol": sym,
        "layer": layer,
        "action": "wait",
        "score": round(score, 4),
        "target_usd": target,
        "stop_usd": round(
            abs(
                stop_loss_usd(
                    target,
                    stop_target_mult_effective=float(
                        get_params(sym).get("stop_target_mult_effective") or stop_target_mult()
                    ),
                )
            ),
            4,
        ),
        "eod_phase": phase,
        "reasoning": "no_edge",
        "confidence": abs(score),
    }
    if ant and ant.get("enabled"):
        out["movement_anticipation"] = {
            "regime": ant.get("regime"),
            "bias": ant.get("bias"),
            "confidence": ant.get("confidence"),
            "hypothesis_id": ant.get("hypothesis_id"),
        }

    if is_force_flatten_window() or phase == "force_flatten":
        if side != "flat":
            out["action"] = "flatten"
            out["reasoning"] = "eod_force_flatten"
        return out

    if side == "flat" and sym in runtime_denylist():
        out["reasoning"] = "manual_denylist"
        return out

    params_early = get_params(sym)
    stop_mult = float(params_early.get("stop_target_mult_effective") or stop_target_mult())
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

    if side in ("long", "short") and unreal is not None:
        u = _f(unreal)
        symbol_state["peak_unrealized"] = max(peak, u)
        peak = _f(symbol_state.get("peak_unrealized"))
        stop_usd = stop_loss_usd(target, stop_target_mult_effective=stop_mult)
        if time_stop_triggered(symbol_state, unrealized=u, target_usd=target):
            out["action"] = "exit_position"
            out["reasoning"] = "time_stop"
            return out
        if u >= target:
            out["action"] = "exit_position"
            out["reasoning"] = f"infra_target_hit:{u:.3f}>={target:.3f}"
            return out
        if u > 0 and peak >= target * 0.6 and u < peak * 0.55:
            out["action"] = "exit_position"
            out["reasoning"] = "trailing_giveback"
            return out
        if u <= stop_usd:
            out["action"] = "exit_position"
            out["reasoning"] = f"stop_loss:{u:.3f}"
            return out
        stack_stress = int(features.get("stack_stress") or 0)
        if stack_stress >= 4 and u > target * 0.45:
            out["action"] = "exit_position"
            out["reasoning"] = "stack_unwind_take_profit"
            return out
        out["reasoning"] = f"hold_{side}:{u:.3f}"
        return out

    if side != "flat":
        return out

    # halt_allows_exits: swarm_halted gates new entries only
    if swarm_halted:
        out["reasoning"] = "swarm_halted"
        return out

    if open_positions >= max_open:
        out["reasoning"] = "max_open_positions"
        return out

    spread_limit = max_spread_bps() * float(params_early.get("spread_bps_mult") or 1.0)
    feat_spread = features.get("spread_bps")
    if feat_spread is not None and _f(feat_spread) > spread_limit:
        out["reasoning"] = f"spread_too_wide:{_f(feat_spread):.1f}>{spread_limit:.1f}"
        return out
    if last <= 0:
        out["reasoning"] = "no_price"
        return out

    params = get_params(sym)
    pd = params.get("pattern_deltas") or {}
    enter_long = float(params["enter_long"])
    enter_short = float(params["enter_short"])
    stop_usd_val = stop_loss_usd(target, stop_target_mult_effective=stop_mult)
    if sym in high_vol_symbols():
        enter_long += 0.04
        enter_short -= 0.04

    l1_imp = _f(features.get("lead_impulse_l1"))
    lag = _f(features.get("propagation_lag_vs_l1"))
    res_layer = _f(features.get("residual_vs_layer"))
    r5 = _f(features.get("r5m"))
    r1 = _f(features.get("r1m"))
    impulse = lead_impulse_threshold()
    lag_tol = propagation_lag_tolerance()

    # layer_catch_up_long — L1 impulse up, symbol lagging layer
    if layer != "L1" and l1_imp >= impulse and lag >= lag_tol and res_layer <= -lag_tol * 0.5:
        th = enter_long + float(pd.get("layer_catch_up_long") or 0)
        if score >= th:
            hit = _try_entry(
                out,
                sym=sym,
                features=features,
                score=score,
                pattern="layer_catch_up_long",
                side="long",
                action="enter_long",
                reasoning=f"layer_catch_up_long score={score:.2f} lag={lag:.4f}",
                params=params,
                target_usd=target,
                stop_usd=stop_usd_val,
            )
            if hit is not None:
                return hit

    # layer_catch_up_short
    if layer != "L1" and l1_imp <= -impulse and lag <= -lag_tol and res_layer >= lag_tol * 0.5:
        th = enter_short + float(pd.get("layer_catch_up_short") or 0)
        if score <= th:
            hit = _try_entry(
                out,
                sym=sym,
                features=features,
                score=score,
                pattern="layer_catch_up_short",
                side="short",
                action="enter_short",
                reasoning=f"layer_catch_up_short score={score:.2f}",
                params=params,
                target_usd=target,
                stop_usd=stop_usd_val,
            )
            if hit is not None:
                return hit

    # layer_rip_fade — symbol rich vs layer basket
    rip_th = enter_short + float(pd.get("layer_rip_fade") or 0)
    if res_layer >= lag_tol * 2 and r5 > 0 and r1 < 0.0008:
        if score <= rip_th:
            hit = _try_entry(
                out,
                sym=sym,
                features=features,
                score=score,
                pattern="layer_rip_fade",
                side="short",
                action="enter_short",
                reasoning=f"layer_rip_fade score={score:.2f} res={res_layer:.4f}",
                params=params,
                target_usd=target,
                stop_usd=stop_usd_val,
            )
            if hit is not None:
                return hit

    # equipment_capex_confirm — L3 pullback in L1+L3 uptrend
    if layer == "L3" and l1_imp > impulse * 0.6 and _f(features.get("layer_r5m")) > 0 and r5 > 0 and r1 < 0:
        th = enter_long + 0.06 + float(pd.get("equipment_capex_confirm") or 0)
        if score >= th:
            hit = _try_entry(
                out,
                sym=sym,
                features=features,
                score=score,
                pattern="equipment_capex_confirm",
                side="long",
                action="enter_long",
                reasoning=f"equipment_capex_confirm score={score:.2f}",
                params=params,
                target_usd=target,
                stop_usd=stop_usd_val,
            )
            if hit is not None:
                return hit

    # power_parity — L4 enabler lead when L1 flat
    if layer == "L4" and abs(l1_imp) < impulse * 0.35 and r5 > 0.0006:
        th = enter_long + 0.05 + float(pd.get("power_parity") or 0)
        if score >= th:
            hit = _try_entry(
                out,
                sym=sym,
                features=features,
                score=score,
                pattern="power_parity",
                side="long",
                action="enter_long",
                reasoning=f"power_parity score={score:.2f}",
                params=params,
                target_usd=target,
                stop_usd=stop_usd_val,
            )
            if hit is not None:
                return hit

    # L1 momentum when stack aligned
    if layer == "L1" and l1_imp >= impulse and int(features.get("stack_stress") or 0) >= 2:
        th = enter_long + 0.08 + float(pd.get("stack_momentum_long") or 0)
        if score >= th and r5 > 0:
            hit = _try_entry(
                out,
                sym=sym,
                features=features,
                score=score,
                pattern="stack_momentum_long",
                side="long",
                action="enter_long",
                reasoning=f"stack_momentum_long score={score:.2f}",
                params=params,
                target_usd=target,
                stop_usd=stop_usd_val,
            )
            if hit is not None:
                return hit

    out["reasoning"] = f"no_entry score={score:.2f} layer={layer}"
    return out
