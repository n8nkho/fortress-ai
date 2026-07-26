"""Track per-session entry block counts and evaluate macro entry guards."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from utils.portfolio_session.entry_block_breakdown import (
    increment_market_relative_underperformance_breakdown,
)
from utils.portfolio_session.entry_decision import evaluate_entry_decision
from utils.portfolio_session.guards import GUARD_REGISTRY

log = logging.getLogger(__name__)

_BLOCK_TYPES = (
    "denylist",
    "pause_entries",
    "pattern_disables",
    "market_relative",
    "market_relative_underperformance",
)


def _classify_block(reasoning: str) -> str | None:
    r = str(reasoning or "").strip().lower()
    if not r:
        return None
    if r == "manual_denylist" or r.endswith("_denylist") or r == "denylist":
        return "denylist"
    if r in ("pause_entries", "swarm_session_critical_pause") or r.startswith("pause_"):
        return "pause_entries"
    if r.startswith("pattern_disabled") or "pattern_disable" in r:
        return "pattern_disables"
    if r == "constructive_tape_entry_override" or "constructive_tape" in r or "tape_override" in r:
        return None
    if r == "market_relative_underperformance" or "market_relative" in r:
        return "market_relative_underperformance"
    return None


class EntryManager:
    def __init__(self) -> None:
        self._block_counts: dict[str, int] = {k: 0 for k in _BLOCK_TYPES}

    def evaluate_entry_blocks(
        self,
        reasoning: str,
        *,
        action: str = "wait",
        side: str = "flat",
        executed: bool | None = None,
    ) -> str | None:
        """Increment block counter when a flat-side entry attempt is rejected."""
        if str(side or "flat") != "flat":
            return None
        act = str(action or "wait")
        if act in ("enter_long", "enter_short") and executed is not False:
            return None
        block_type = _classify_block(reasoning)
        if not block_type:
            return None
        self._block_counts[block_type] = int(self._block_counts.get(block_type) or 0) + 1
        # Legacy alias used by session reports / older tests.
        if block_type == "market_relative_underperformance":
            self._block_counts["market_relative"] = (
                int(self._block_counts.get("market_relative") or 0) + 1
            )
        return block_type

    def block_counts(self) -> dict[str, int]:
        return dict(self._block_counts)

    def reset_counts(self) -> None:
        self._block_counts = {k: 0 for k in _BLOCK_TYPES}


_manager = EntryManager()


def get_entry_manager() -> EntryManager:
    return _manager


def record_market_relative_block() -> None:
    """Increment market_relative counter when MarketRelativeGate blocks an entry."""
    mgr = get_entry_manager()
    counts = increment_market_relative_underperformance_breakdown(mgr._block_counts)
    mgr._block_counts.update(counts)
    log.info(
        "entry_block_breakdown market_relative_underperformance=%s market_relative=%s "
        "marker=market_relative_underperformance",
        mgr._block_counts.get("market_relative_underperformance"),
        mgr._block_counts.get("market_relative"),
    )


def record_entry_block(
    decision: dict[str, Any],
    act_result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
) -> str | None:
    """Convenience hook for swarm workers after decide/act."""
    side = str((features or {}).get("side") or "flat")
    action = str(decision.get("action") or "wait")
    reasoning = str(act_result.get("block_reason") or decision.get("reasoning") or "")
    executed = act_result.get("executed")
    return get_entry_manager().evaluate_entry_blocks(
        reasoning,
        action=action,
        side=side,
        executed=executed if executed is not None else None,
    )


@dataclass
class EntryManagerDecision:
    blocked: bool = False
    reason: str = ""
    detail: str = ""
    guard: str = ""


def evaluate_entry(
    context: dict[str, Any] | None = None,
    *,
    prior_block_reason: str = "",
) -> tuple[EntryManagerDecision, dict[str, Any]]:
    """Run entry guard chain via guard registry (market_relative_underperformance)."""
    decision, state = evaluate_entry_decision(context, prior_block_reason=prior_block_reason)
    if decision.blocked:
        log.warning(
            "entry_blocked_by_market_relative market_relative_underperformance "
            "MarketRelativeGate swarm_gate_order_specific_before_macro %s "
            "entry_block_breakdown=%s",
            decision.detail or decision.reason,
            state.get("entry_block_breakdown"),
        )
    return (
        EntryManagerDecision(
            blocked=decision.blocked,
            reason=decision.reason,
            detail=decision.detail,
            guard=decision.guard,
        ),
        state,
    )


def check_guard(name: str, context: dict[str, Any] | None = None) -> EntryManagerDecision:
    """Evaluate a named guard from GUARD_REGISTRY (SI entry_manager integration)."""
    guard_cls = GUARD_REGISTRY.get(name)
    if guard_cls is None:
        return EntryManagerDecision(blocked=False, guard=name)

    guard = guard_cls()
    state = dict(context or {})
    check_fn = getattr(guard, "check_session_context", None)
    if callable(check_fn):
        result = check_fn(state)
    else:
        result = guard.evaluate(state)
    if not result.blocked:
        return EntryManagerDecision(blocked=False, guard=name)

    log.warning(
        "entry_blocked_by_market_relative market_relative_underperformance "
        "MarketRelativeGate %s",
        result.detail or result.reason,
    )
    return EntryManagerDecision(
        blocked=True,
        reason=result.reason or name,
        detail=result.detail or "",
        guard=result.guard or name,
    )
