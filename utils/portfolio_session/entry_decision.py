"""Portfolio session entry decision pipeline — guard chain after order-specific blocks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from utils.portfolio_session.entry_block_breakdown import (
    increment_market_relative_underperformance_breakdown,
)
from utils.portfolio_session.config import (
    get_market_relative_underperformance_enabled,
    get_market_relative_underperformance_threshold,
)
from utils.portfolio_session.entry_guard_manager import get_entry_guards
from utils.portfolio_session.entry_guards import market_relative_underperformance_gate
from utils.portfolio_session.session_metrics import build_session_metrics
from utils.portfolio_session.session_monitor import update_session_metrics

log = logging.getLogger(__name__)

_PAUSE_REASONS = frozenset(
    {
        "pause_entries",
        "swarm_session_critical_pause",
    }
)


@dataclass
class EntryDecision:
    blocked: bool = False
    reason: str = ""
    detail: str = ""
    guard: str = ""


def _increment_breakdown(session_state: dict[str, Any], key: str) -> dict[str, Any]:
    if key == "market_relative":
        updated = dict(session_state)
        updated["entry_block_breakdown"] = increment_market_relative_underperformance_breakdown(
            session_state.get("entry_block_breakdown")
        )
        return updated
    breakdown = dict(session_state.get("entry_block_breakdown") or {})
    breakdown[key] = int(breakdown.get(key) or 0) + 1
    updated = dict(session_state)
    updated["entry_block_breakdown"] = breakdown
    return updated


def _order_specific_blocks_active(session_state: dict[str, Any], prior_block_reason: str) -> bool:
    """Return True when this entry path is already blocked by pause/pattern (caller-supplied).

    Do not treat cumulative session entry_block_breakdown counters as an active block —
    those are historical tallies and must not skip the market-relative macro gate.
    """
    reason = str(prior_block_reason or session_state.get("prior_block_reason") or "").strip().lower()
    if reason in _PAUSE_REASONS or reason.startswith("pause_"):
        return True
    if reason.startswith("pattern_disabled") or reason == "pattern_disables":
        return True
    return False


def evaluate_entry_decision(
    session_state: dict[str, Any] | None = None,
    *,
    prior_block_reason: str = "",
) -> tuple[EntryDecision, dict[str, Any]]:
    """Run guard chain after pause_entries and pattern_disables; market-relative runs last."""
    state = update_session_metrics(build_session_metrics(dict(session_state or {})), force=True)

    # swarm_gate_order_specific_before_macro: macro gate follows per-symbol blocks
    if _order_specific_blocks_active(state, prior_block_reason):
        return EntryDecision(blocked=False, guard="order_specific"), state

    if not get_market_relative_underperformance_enabled():
        return EntryDecision(blocked=False, guard="market_relative_underperformance"), state

    if not state.get("benchmark_ok", True):
        return EntryDecision(blocked=False, guard="market_relative_underperformance"), state

    alpha = state.get("alpha_vs_spy_pct")
    if alpha is None:
        alpha = state.get("session_alpha_vs_spy")
    if alpha is None:
        return EntryDecision(blocked=False, guard="market_relative_underperformance"), state

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

        state = _increment_breakdown(state, "market_relative")
        state["entry_block_reason"] = result.reason or "market_relative_underperformance"
        log.warning(
            "entry_blocked_by_market_relative market_relative_underperformance MarketRelativeGate "
            "swarm_gate_order_specific_before_macro entry_block_breakdown=%s %s",
            state.get("entry_block_breakdown"),
            result.detail or result.reason,
        )
        return (
            EntryDecision(
                blocked=True,
                reason=result.reason or "market_relative_underperformance",
                detail=result.detail or "",
                guard=guard.name,
            ),
            state,
        )

    threshold = get_market_relative_underperformance_threshold()
    if market_relative_underperformance_gate(float(alpha), threshold):
        try:
            from utils.portfolio_session.constructive_tape_override import (
                maybe_allow_despite_underperformance,
            )

            allow, ov_detail = maybe_allow_despite_underperformance(
                float(alpha),
                hard_threshold=float(threshold),
                session_state=state,
            )
            if allow:
                return (
                    EntryDecision(
                        blocked=False,
                        reason="constructive_tape_entry_override",
                        detail=ov_detail,
                        guard="market_relative_underperformance",
                    ),
                    state,
                )
        except Exception:
            pass
        state = _increment_breakdown(state, "market_relative")
        reason = "market_relative_underperformance"
        state["entry_block_reason"] = reason
        bps = int(round(abs(threshold) * 100))
        detail = (
            f"session_underperforming alpha_vs_spy={float(alpha):.4f} "
            f"market_relative_underperformance_threshold={threshold:.4f} "
            f"market_relative_underperformance_threshold_bps={bps}"
        )
        log.warning(
            "entry_blocked_by_market_relative market_relative_underperformance MarketRelativeGate "
            "swarm_gate_order_specific_before_macro session_underperforming entry_block_breakdown=%s %s",
            state.get("entry_block_breakdown"),
            detail,
        )
        return (
            EntryDecision(blocked=True, reason=reason, detail=detail, guard="market_relative_underperformance"),
            state,
        )

    detail = f"alpha_vs_spy={float(alpha):.4f}"
    return EntryDecision(blocked=False, guard="market_relative_underperformance", detail=detail), state
