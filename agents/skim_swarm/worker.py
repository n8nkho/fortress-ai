"""Single-symbol skim worker."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from agents.skim_swarm.coordinator import EntrySlotGuard
from agents.skim_swarm.act import act, cooldown_until_seconds
from agents.skim_swarm.company_context import format_context_blurb
from agents.skim_swarm.features import build_symbol_features
from agents.skim_swarm.intraday_si import log_shadow_decision, shadow_target_mult
from agents.skim_swarm.signal import decide
from agents.skim_swarm.state import load_symbol_state, save_symbol_state
from agents.skim_swarm.symbol_learning import (
    default_cooldown_sec,
    get_causation,
    get_params,
    load_learned,
    record_decision,
)
import agents.skim_swarm.symbol_learning as sym_learn
from utils.skim_swarm_config import shadow_lane_enabled


def run_symbol_cycle(
    symbol: str,
    *,
    bars: dict,
    shared: dict[str, Any],
    account: dict[str, Any],
    swarm: dict[str, Any],
    open_count: int,
    max_open: int,
    company_context: dict[str, Any] | None = None,
    entry_guard: EntrySlotGuard | None = None,
) -> dict[str, Any]:
    sym = symbol.upper()
    positions = account.get("positions") or {}
    pos = positions.get(sym) or {"symbol": sym, "qty": 0, "side": "flat"}
    st = load_symbol_state(sym)
    st["side"] = pos.get("side") or st.get("side") or "flat"

    ctx = company_context or {}
    features = build_symbol_features(sym, bars, shared, position=pos, company_context=ctx)
    features["position_qty"] = int(pos.get("qty") or 0)
    try:
        features["buying_power_usd"] = float(account.get("buying_power"))
    except (TypeError, ValueError):
        pass
    learned = load_learned(sym)
    halted = bool(swarm.get("halted"))
    halt_reason = swarm.get("halt_reason")
    if halt_reason and "semi_long" in str(halt_reason) and features.get("symbol") == "MSFT":
        if (features.get("side") or "flat") == "flat":
            halted = True

    decision = decide(
        features,
        st,
        swarm_halted=halted,
        open_positions=open_count,
        max_open=max_open,
    )

    if shadow_lane_enabled():
        params = learned.get("params") or {}
        sh_tm = shadow_target_mult(params, learned)
        shadow_params = {
            **get_params(sym),
            "target_mult": sh_tm,
            "target_mult_effective": sh_tm * float((learned.get("session_overlay") or {}).get("target_mult_overlay") or 1.0),
        }
        with patch.object(sym_learn, "get_params", return_value=shadow_params):
            shadow_decision = decide(
                features,
                dict(st),
                swarm_halted=halted,
                open_positions=open_count,
                max_open=max_open,
            )
        log_shadow_decision(
            sym,
            {
                "live_action": decision.get("action"),
                "shadow_action": shadow_decision.get("action"),
                "live_reasoning": decision.get("reasoning"),
                "shadow_reasoning": shadow_decision.get("reasoning"),
                "shadow_target_mult": sh_tm,
            },
        )

    act_result: dict[str, Any]
    action = decision.get("action")
    if action in ("enter_long", "enter_short"):
        reserved = False
        if entry_guard is not None and not entry_guard.try_reserve():
            act_result = {
                "action": action,
                "executed": False,
                "detail": "max_open_positions",
                "block_reason": "max_open_positions",
            }
        else:
            reserved = entry_guard is not None
            act_result = act(
                decision,
                symbol=sym,
                equity=float(account.get("equity") or 0),
                position=pos,
            )
            if reserved and not act_result.get("executed"):
                entry_guard.release()
    else:
        act_result = act(
            decision,
            symbol=sym,
            equity=float(account.get("equity") or 0),
            position=pos,
        )

    # Update local state after act
    st["last_action"] = decision.get("action")
    st["last_block_reason"] = act_result.get("block_reason") or decision.get("reasoning")
    if act_result.get("executed"):
        action = decision.get("action")
        now_iso = datetime.now(timezone.utc).isoformat()
        if action in ("enter_long", "enter_short"):
            st["side"] = "long" if action == "enter_long" else "short"
            st["entry_price"] = features.get("last")
            st["entry_ts"] = now_iso
            st["last_clip_ts"] = now_iso
            st["peak_unrealized"] = 0.0
        elif action in ("add_clip_long", "add_clip_short"):
            st["last_clip_ts"] = now_iso
        elif action == "exit_partial":
            st["last_exit_ts"] = now_iso
            if int(pos.get("qty") or 0) <= int(decision.get("exit_qty") or 1):
                st["side"] = "flat"
                st["entry_price"] = None
                st["entry_ts"] = None
                st["peak_unrealized"] = 0.0
                st["cooldown_until_utc"] = cooldown_until_seconds(default_cooldown_sec(sym))
            else:
                st["peak_unrealized"] = 0.0
        elif action in ("exit_position", "flatten"):
            st["side"] = "flat"
            st["entry_price"] = None
            st["entry_ts"] = None
            st["last_exit_ts"] = now_iso
            st["peak_unrealized"] = 0.0
            st["cooldown_until_utc"] = cooldown_until_seconds(default_cooldown_sec(sym))
    save_symbol_state(st)

    improvement = record_decision(
        sym,
        decision=decision,
        act_result=act_result,
        features=features,
    )

    try:
        from utils.portfolio_session.entry_manager import record_entry_block

        record_entry_block(decision, act_result, features=features)
    except Exception:
        pass

    try:
        from utils.session_diary import record_swarm_event

        record_swarm_event(
            component="skim_swarm",
            symbol=sym,
            decision=decision,
            act_result=act_result,
            features=features,
        )
    except Exception:
        pass

    beta = features.get("company_beta")
    params = learned.get("params") or {}
    tm = float(params.get("target_mult") or 1.0)
    slow_lane = (
        beta is not None and float(beta) > 1.25 and (features.get("side") or "flat") != "flat"
    ) or tm > 1.12

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "features": {
            k: features.get(k)
            for k in ("last", "r1m", "r5m", "residual_vs_spy", "unrealized_usd", "side", "thin_etf")
        },
        "decision": decision,
        "act": act_result,
        "learned_stats": learned.get("session_stats"),
        "learned_params": learned.get("params"),
        "causation": get_causation(sym),
        "company_blurb": format_context_blurb(ctx),
        "improvement": improvement,
        "fast_loop": (features.get("side") or "flat") != "flat",
        "slow_lane": slow_lane,
    }
