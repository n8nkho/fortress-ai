"""Portfolio session entry block manager — macro guards before swarm entries."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.config import (
    get_market_relative_underperformance_enabled,
    get_market_relative_underperformance_threshold,
    load_market_relative_guard_config,
)
from utils.portfolio_session.entry_guards.market_relative_underperformance import (
    check_market_relative_underperformance,
)
from utils.portfolio_session.guards import GUARD_REGISTRY, BaseGuard, MarketRelativeUnderperformanceGuard
from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha

log = logging.getLogger(__name__)


class EntryBlockManager:
    """Evaluate portfolio-session macro entry blocks (market-relative underperformance first)."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or load_market_relative_guard_config())

    def _build_guards(self) -> list[BaseGuard]:
        """Return guards in evaluation order (market-relative underperformance first)."""
        guard_cls = GUARD_REGISTRY.get(
            "market_relative_underperformance",
            GUARD_REGISTRY.get("market_relative", MarketRelativeUnderperformanceGuard),
        )
        threshold = self.config.get(
            "underperformance_threshold",
            self.config.get(
                "underperformance_threshold_pct",
                self.config.get(
                    "market_relative_underperformance_threshold_pct",
                    self.config.get("threshold_alpha_pp", self.config.get("threshold_pct", -0.5)),
                ),
            ),
        )
        return [
            guard_cls(
                config=self.config,
                underperformance_threshold=threshold,
                enabled=bool(self.config.get("enabled", True)),
            )
        ]

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

        threshold = get_market_relative_underperformance_threshold()
        for guard in self._build_guards():
            block_reason = check_market_relative_underperformance(state, threshold)
            if not block_reason:
                continue

            breakdown = dict(state.get("entry_block_breakdown") or {})
            breakdown["market_relative"] = int(breakdown.get("market_relative") or 0) + 1
            state["entry_block_breakdown"] = breakdown
            state["entry_block_reason"] = "market_relative_underperformance"
            log.warning(
                "entry_blocked_by_market_relative market_relative_underperformance "
                "MarketRelativeGate swarm_gate_order_specific_before_macro %s",
                block_reason,
            )
            return {
                "blocked": True,
                "reason": "market_relative_underperformance",
                "market_relative_underperformance": True,
                "entry_block_breakdown": breakdown,
                "detail": block_reason,
            }

        return empty


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
