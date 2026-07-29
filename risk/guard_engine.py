"""Evaluate portfolio session entry guards before swarm entries."""
from __future__ import annotations

import logging
from typing import Any

from risk.guards.base import BaseGuard
from risk.guards.market_relative_guard import MarketRelativeGuard
from utils.portfolio_session.entry_block_breakdown import (
    increment_market_relative_underperformance_breakdown,
)
from utils.portfolio_session.risk_manager import build_session_state
from utils.portfolio_session.session_monitor import update_session_metrics

log = logging.getLogger(__name__)

PRE_TRADE_GUARDS: list[type[BaseGuard]] = [
    MarketRelativeGuard,
]


def evaluate_entry(session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run pre-trade guards; increment entry_block_breakdown.market_relative when blocked."""
    state = update_session_metrics(build_session_state(session_state=session_state))
    state.setdefault("component", "portfolio_session")
    breakdown = dict(state.get("entry_block_breakdown") or {})

    alpha = state.get("session_alpha_vs_spy")
    if alpha is None:
        alpha = state.get("alpha_vs_spy_pct")

    for guard_cls in PRE_TRADE_GUARDS:
        guard = guard_cls()
        if alpha is None:
            continue
        if guard.check(float(alpha)):
            breakdown = increment_market_relative_underperformance_breakdown(breakdown)
            state["entry_block_breakdown"] = breakdown
            state["entry_block_reason"] = "market_relative_underperformance"
            detail = (
                f"session_underperforming alpha_vs_spy={float(alpha):.4f} "
                f"market_relative_underperformance_threshold="
                f"{getattr(guard, 'threshold_alpha_pct', -0.5):.4f}"
            )
            log.info("entry_blocked_by_market_relative %s", detail)
            return {
                "blocked": True,
                "entry_block_reason": "market_relative_underperformance",
                "detail": detail,
                "denylist": ["*"],
                "entry_block_breakdown": breakdown,
                "session_state": state,
            }

    return {
        "blocked": False,
        "entry_block_reason": "",
        "detail": "",
        "denylist": [],
        "entry_block_breakdown": breakdown,
        "session_state": state,
    }


def evaluate_entry_guards(session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Backward-compatible wrapper for swarm signal integration."""
    result = evaluate_entry(session_state=session_state)
    return {
        "blocked": result["blocked"],
        "entry_block_reason": result.get("entry_block_reason") or "",
        "detail": result.get("detail") or "",
        "denylist": result.get("denylist") or ([] if not result["blocked"] else ["*"]),
    }
