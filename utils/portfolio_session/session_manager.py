"""Portfolio session manager — macro entry guard evaluation."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.entry_block_breakdown import (
    increment_market_relative_underperformance_breakdown,
)
from utils.portfolio_session.alpha_monitor import check_session_alpha
from utils.portfolio_session.config import get_beta_leaders
from utils.portfolio_session.entry_guard_manager import get_entry_guards
from utils.portfolio_session.guards.base import GuardResult
from utils.portfolio_session.guards.market_relative_underperformance import (
    MarketRelativeUnderperformanceGuard,
)
from utils.portfolio_session.risk_manager import load_market_relative_gate_config
from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha
from utils.portfolio_session.risk_manager import build_session_state
from utils.portfolio_session.session_monitor import update_session_metrics

log = logging.getLogger(__name__)

_negative_alpha_disabled_symbols: set[str] = set()


def _compute_session_alpha(session_state: dict[str, Any]) -> dict[str, Any]:
    """Derive session alpha vs SPY when return fields are present."""
    return enrich_session_context_with_alpha(session_state)


def _compute_alpha_vs_spy(session_context: dict[str, Any]) -> float | None:
    """Return session alpha vs SPY from explicit fields or session P&L vs benchmark change."""
    from utils.portfolio_session.metrics.session_alpha import compute_session_alpha_vs_spy

    return compute_session_alpha_vs_spy(session_context)


def is_symbol_disabled_for_negative_alpha(symbol: str) -> bool:
    sym = str(symbol or "").strip().upper()
    return bool(sym) and sym in _negative_alpha_disabled_symbols


def disable_symbol_for_negative_alpha(symbol: str) -> None:
    sym = str(symbol or "").strip().upper()
    if sym:
        _negative_alpha_disabled_symbols.add(sym)


def reset_negative_alpha_disabled_symbols() -> None:
    _negative_alpha_disabled_symbols.clear()


def finalize_session_after_exit(
    session_data: dict[str, Any] | None = None,
    *,
    symbol: str | None = None,
    spy_returns: Any = None,
) -> dict[str, Any]:
    """After the 3rd exit, flag negative alpha and optionally disable symbol entries."""
    state = dict(session_data or {})
    exit_count = int(state.get("exit_count") or state.get("session_exit_count") or 0)
    if exit_count < 3:
        return state

    alpha_result = check_session_alpha(state, spy_returns)
    state.update(alpha_result)
    if not alpha_result.get("negative_alpha_active_session"):
        return state

    sym = str(symbol or state.get("symbol") or "").strip().upper()
    beta_leaders = get_beta_leaders()
    log.warning(
        "negative_alpha_active_session symbol=%s alpha_vs_spy_pct=%s exit_count=%s tape_trend=%s beta_leader=%s",
        sym or "?",
        alpha_result.get("alpha_vs_spy_pct"),
        exit_count,
        alpha_result.get("tape_trend"),
        sym in beta_leaders if sym else False,
    )
    if sym and sym not in beta_leaders:
        disable_symbol_for_negative_alpha(sym)
        state["entry_block_reason"] = "negative_alpha_active_session"
        state["negative_alpha_symbol_disabled"] = sym
    return state


def _evaluate_entry_guards(session_context: dict[str, Any] | None = None) -> tuple[GuardResult, dict[str, Any]]:
    """Run portfolio session macro guards and merge block into session context."""
    state = _compute_session_alpha(
        update_session_metrics(build_session_state(session_state=session_context))
    )
    state.setdefault("component", "portfolio_session")
    symbol = str(state.get("symbol") or "").strip().upper()
    if symbol and is_symbol_disabled_for_negative_alpha(symbol):
        breakdown = dict(state.get("entry_block_breakdown") or {})
        breakdown["negative_alpha_active_session"] = int(
            breakdown.get("negative_alpha_active_session") or 0
        ) + 1
        state["entry_block_breakdown"] = breakdown
        state["entry_block_reason"] = "negative_alpha_active_session"
        log.warning(
            "entry_blocked_by_negative_alpha_active_session negative_alpha_active_session symbol=%s",
            symbol,
        )
        return GuardResult(
            blocked=True,
            reason="negative_alpha_active_session",
            detail=f"symbol={symbol}",
            guard="negative_alpha_active_session",
        ), state

    # swarm_gate_order_specific_before_macro: macro market-relative gate follows per-symbol blocks
    for guard in get_entry_guards():
        check_fn = getattr(guard, "check_session_context", None)
        if callable(check_fn):
            result = check_fn(state)
        elif hasattr(guard, "evaluate_session"):
            result = guard.evaluate_session(state)
        else:
            result = guard.evaluate(state)
        if not result.blocked:
            continue

        breakdown = increment_market_relative_underperformance_breakdown(
            state.get("entry_block_breakdown")
        )
        state["entry_block_breakdown"] = breakdown
        state["entry_block_reason"] = result.reason or "market_relative_underperformance"
        log.warning(
            "entry_blocked_by_market_relative market_relative_underperformance MarketRelativeGate %s",
            result.detail or result.reason,
        )
        return GuardResult(
            blocked=True,
            reason=result.reason or "market_relative_underperformance",
            detail=result.detail,
            guard=guard.name,
        ), state

    alpha = state.get("alpha_vs_spy_pct")
    if alpha is None:
        alpha = state.get("session_alpha_vs_spy")
    detail = f"alpha_vs_spy={alpha:.4f}" if alpha is not None else ""
    return GuardResult(blocked=False, guard="market_relative_underperformance", detail=detail), state


def evaluate_entry_blocks(
    session_state: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Evaluate macro market-relative block after order-specific blocks (pattern_disables, …)."""
    from utils.portfolio_session.entry_decision import evaluate_entry_decision

    decision, state = evaluate_entry_decision(session_state)
    if decision.blocked:
        alpha = state.get("alpha_vs_spy_pct")
        if alpha is None:
            alpha = state.get("session_alpha_vs_spy")
        cfg = load_market_relative_gate_config()
        threshold = float(
            cfg.get(
                "threshold_pct",
                cfg.get("underperformance_threshold_pct", cfg.get("max_underperformance_pct", -0.5)),
            )
        )
        try:
            from monitoring.alerts import alert_market_relative_underperformance

            if alpha is not None:
                alert_market_relative_underperformance(
                    alpha_vs_spy_pct=float(alpha),
                    threshold=threshold,
                    symbol=str(state.get("symbol") or ""),
                )
        except ImportError:
            pass
    return decision.blocked, decision.reason, state


def should_block_entry(session_state: dict[str, Any] | None = None) -> bool:
    """Return True when session alpha vs SPY underperforms the configured threshold."""
    from utils.portfolio_session.config import (
        get_market_relative_underperformance_enabled,
        get_market_relative_underperformance_threshold,
    )
    from utils.portfolio_session.guards.market_relative import check_market_relative_underperformance
    from utils.portfolio_session.session_metrics import build_session_metrics

    state = build_session_metrics(dict(session_state or {}))
    if not get_market_relative_underperformance_enabled():
        return False
    if not state.get("benchmark_ok", True):
        return False

    threshold = get_market_relative_underperformance_threshold()
    mag = abs(threshold) if threshold < 0 else threshold

    session_alpha = state.get("session_return_pct")
    if session_alpha is None:
        session_alpha = state.get("session_pnl_pct")
    spy_change = state.get("benchmark_change_1d_pct")
    if spy_change is None:
        spy_change = state.get("spy_return_pct")

    block_reason: str | bool | None = None
    if session_alpha is not None and spy_change is not None:
        block_reason = check_market_relative_underperformance(
            float(session_alpha),
            float(spy_change),
            mag,
        )
    else:
        alpha = state.get("alpha_vs_spy_pct")
        if alpha is None:
            alpha = state.get("session_alpha_vs_spy")
        if alpha is not None:
            block_reason = check_market_relative_underperformance(float(alpha), threshold)

    if not block_reason:
        return False

    reason_str = (
        block_reason if isinstance(block_reason, str) else "market_relative_underperformance"
    )
    breakdown = increment_market_relative_underperformance_breakdown(state.get("entry_block_breakdown"))
    state["entry_block_breakdown"] = breakdown
    state["entry_block_reason"] = "market_relative_underperformance"
    log.warning(
        "entry_blocked_by_market_relative market_relative_underperformance "
        "MarketRelativeGate swarm_gate_order_specific_before_macro %s",
        reason_str,
    )
    return True


def evaluate_entry_guards(session_context: dict[str, Any] | None = None) -> GuardResult:
    """Public entry guard evaluation for swarm and guard engine integration."""
    result, _state = _evaluate_entry_guards(session_context)
    return result
