"""Portfolio session entry block manager — macro guards before swarm entries."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.entry_block_breakdown import (
    increment_market_relative_underperformance_breakdown,
)
from utils.portfolio_session.config import (
    get_market_relative_underperformance_enabled,
    load_market_relative_guard_config,
)
from utils.portfolio_session.pre_trade_gate import check_market_relative_underperformance
from utils.portfolio_session.entry_guard_router import build_entry_guards
from utils.portfolio_session.guards import BaseGuard
from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha

log = logging.getLogger(__name__)


class EntryBlockManager:
    """Evaluate portfolio-session macro entry blocks (market-relative underperformance first)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or load_market_relative_guard_config())

    def _build_guards(self) -> list[BaseGuard]:
        """Return guards in evaluation order (market_relative then underperformance)."""
        return build_entry_guards(self.config)

    def evaluate_blocks(self, session_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate all entry guards; returns block flags and breakdown when macro gate triggers."""
        state = enrich_session_context_with_alpha(dict(session_state or {}))
        state.setdefault("component", "portfolio_session")
        empty: dict[str, Any] = {
            "blocked": False,
            "reason": "",
            "market_relative_underperformance": False,
        }

        if not get_market_relative_underperformance_enabled():
            return empty

        gate_result = check_market_relative_underperformance(state, self.config)
        if not gate_result.blocked:
            return empty

        breakdown = increment_market_relative_underperformance_breakdown(
            state.get("entry_block_breakdown")
        )
        state["entry_block_breakdown"] = breakdown
        state["entry_block_reason"] = "market_relative_underperformance"
        log.warning(
            "entry_blocked_by_market_relative market_relative_underperformance "
            "MarketRelativeGate swarm_gate_order_specific_before_macro %s",
            gate_result.detail or gate_result.reason,
        )
        return {
            "blocked": True,
            "reason": "market_relative_underperformance",
            "market_relative_underperformance": True,
            "entry_block_breakdown": breakdown,
            "detail": gate_result.detail or gate_result.reason,
        }


_default_manager: EntryBlockManager | None = None


def get_entry_block_manager() -> EntryBlockManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = EntryBlockManager()
    return _default_manager


def get_entry_guards() -> list[BaseGuard]:
    """Return active macro entry guards (market-relative underperformance first)."""
    return get_entry_block_manager()._build_guards()


def evaluate_entry_guard_blocks(session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate all entry guards; returns block flags and breakdown when macro gate triggers."""
    return get_entry_block_manager().evaluate_blocks(session_state)


def evaluate_entry_guards_loop(session_state: dict[str, Any] | None = None) -> dict[str, bool]:
    """Backward-compatible block flags keyed by guard name."""
    result = evaluate_entry_guard_blocks(session_state)
    return {
        "market_relative_underperformance": bool(
            result.get("blocked") or result.get("market_relative_underperformance")
        )
    }
