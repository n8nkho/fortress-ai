"""Single-symbol skim worker."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.skim_swarm.coordinator import EntrySlotGuard
from agents.skim_swarm.act import act, cooldown_until_seconds
from agents.skim_swarm.company_context import format_context_blurb
from agents.skim_swarm.features import build_symbol_features
from agents.skim_swarm.signal import decide
from agents.skim_swarm.state import load_symbol_state, save_symbol_state
from agents.skim_swarm.symbol_learning import default_cooldown_sec, get_causation, load_learned, record_decision


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
    learned = load_learned(sym)
    halted = bool(swarm.get("halted"))
    halt_reason = swarm.get("halt_reason")
    if halt_reason and "semi_long" in str(halt_reason) and features.get("symbol") in {"NVDA", "MSFT", "AVGO"}:
        if (features.get("side") or "flat") == "flat":
            halted = True

    decision = decide(
        features,
        st,
        swarm_halted=halted,
        open_positions=open_count,
        max_open=max_open,
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
        if action in ("enter_long", "enter_short"):
            st["side"] = "long" if action == "enter_long" else "short"
            st["entry_price"] = features.get("last")
            st["entry_ts"] = datetime.now(timezone.utc).isoformat()
            st["peak_unrealized"] = 0.0
        elif action in ("exit_position", "flatten"):
            st["side"] = "flat"
            st["entry_price"] = None
            st["entry_ts"] = None
            st["peak_unrealized"] = 0.0
            st["cooldown_until_utc"] = cooldown_until_seconds(default_cooldown_sec(sym))
    save_symbol_state(st)

    improvement = record_decision(
        sym,
        decision=decision,
        act_result=act_result,
        features=features,
    )

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
