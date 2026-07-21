"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.guards.base import BaseGuard, GuardResult
from utils.portfolio_session.guards.market_relative import (
    MarketRelativeGuard,
    _compute_alpha_vs_spy,
    _normalize_underperformance_threshold,
    check_market_relative_underperformance,
)

log = logging.getLogger(__name__)

_DEFAULT_UNDERPERFORMANCE_THRESHOLD = 0.5


def should_block_entry(session_alpha_vs_spy: float, threshold: float = -0.5) -> bool:
    """Return True if session alpha vs SPY underperforms beyond threshold (percent points)."""
    return check_market_relative_underperformance(session_alpha_vs_spy, threshold)


class MarketRelativeUnderperformanceGuard(MarketRelativeGuard):
    """Block swarm entries when session alpha vs SPY (1d) is below a configurable threshold."""

    name = "market_relative_underperformance"

    def __init__(
        self,
        *,
        underperformance_threshold: float | None = None,
        enabled: bool | None = None,
        config: dict[str, Any] | None = None,
        threshold_pct: float | None = None,
        threshold_alpha_vs_spy_pct: float | None = None,
        threshold: float | None = None,
        **_: Any,
    ) -> None:
        cfg = dict(config or {})
        raw = underperformance_threshold
        if raw is None:
            raw = cfg.get("underperformance_threshold")
        if raw is None:
            raw = cfg.get("underperformance_threshold_pct")
        if raw is None:
            raw = cfg.get("market_relative_underperformance_threshold")
        if raw is None:
            raw = cfg.get("market_relative_underperformance_threshold_pct")
        if raw is None and threshold_alpha_vs_spy_pct is not None:
            raw = threshold_alpha_vs_spy_pct
        if raw is None:
            raw = cfg.get("threshold_alpha_vs_spy_pct")
        if raw is None and threshold_pct is not None:
            raw = threshold_pct
        if raw is None and threshold is not None:
            raw = threshold
        if raw is None:
            raw = cfg.get(
                "threshold_pct",
                cfg.get("max_underperformance_pct", cfg.get("threshold_alpha_pp", -0.5)),
            )
        self.underperformance_threshold = _normalize_underperformance_threshold(
            raw if raw is not None else _DEFAULT_UNDERPERFORMANCE_THRESHOLD
        )
        self.enabled = bool(enabled if enabled is not None else cfg.get("enabled", True))
        component = cfg.get("component")
        self.component = str(component).strip() if component else None

    def evaluate(self, session_context: dict[str, Any]) -> GuardResult:
        if not self.enabled:
            return GuardResult(blocked=False, guard=self.name)

        if self.component:
            session_component = session_context.get("component")
            if session_component is None:
                return GuardResult(blocked=False, guard=self.name, detail="component_skipped")
            if str(session_component).strip() != self.component:
                return GuardResult(blocked=False, guard=self.name, detail="component_mismatch")

        if not session_context.get("benchmark_ok", True):
            return GuardResult(blocked=False, guard=self.name, detail="benchmark_unavailable")

        alpha_vs_spy = _compute_alpha_vs_spy(session_context)
        benchmark_change = session_context.get("benchmark_change_1d_pct")
        if alpha_vs_spy is None:
            return GuardResult(blocked=False, guard=self.name, detail="missing_alpha_data")

        limit = -self.underperformance_threshold
        if alpha_vs_spy < limit:
            detail = (
                f"session_underperforming alpha_vs_spy={alpha_vs_spy:.4f} "
                f"benchmark_change_1d_pct={benchmark_change} "
                f"market_relative_underperformance_threshold={limit:.4f} "
                f"market_relative_underperformance_threshold_bps="
                f"{int(round(self.underperformance_threshold * 100))}"
            )
            session_id = session_context.get("session_id") or session_context.get("session_key") or "?"
            log.warning(
                "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative "
                "session_id=%s %s",
                session_id,
                detail,
            )
            return GuardResult(
                blocked=True,
                reason="market_relative_underperformance",
                detail=detail,
                guard=self.name,
            )

        return GuardResult(
            blocked=False,
            guard=self.name,
            detail=f"alpha_vs_spy={alpha_vs_spy:.4f}",
        )

    def __call__(self, session_context: dict[str, Any]) -> bool:
        """Return True when entry should be blocked (alpha vs SPY below threshold)."""
        return self.evaluate(session_context).blocked

    def check_session_context(self, session_context: dict[str, Any]) -> GuardResult:
        """Evaluate session_context and return GuardResult (entry pipeline helper)."""
        result = self.evaluate(session_context)
        if not result.blocked:
            return result
        alpha = _compute_alpha_vs_spy(session_context)
        delta = f"{alpha:.2f}" if alpha is not None else "?"
        return GuardResult(
            blocked=True,
            reason=result.reason or "market_relative_underperformance",
            detail=result.detail
            or (
                f"Session underperformed SPY by {delta}% "
                f"market_relative_underperformance_threshold={-self.underperformance_threshold:.4f}"
            ),
            guard=self.name,
        )

    def check(
        self,
        session_alpha_vs_spy: float | dict | None = None,
        threshold: float | None = None,
        *,
        session_state: dict | None = None,
    ) -> tuple[bool, str]:
        """Return (block, reason) when session alpha vs SPY is below threshold."""
        if isinstance(session_alpha_vs_spy, dict):
            blocked = super().check(session_alpha_vs_spy)
            if blocked:
                result = self.evaluate(session_alpha_vs_spy)
                return True, result.reason or "market_relative_underperformance"
            return False, ""
        state = dict(session_state or {})
        if session_alpha_vs_spy is not None:
            state.setdefault("alpha_vs_spy_pct", session_alpha_vs_spy)
            state.setdefault("session_alpha_vs_spy", session_alpha_vs_spy)
        if threshold is not None:
            guard = MarketRelativeUnderperformanceGuard(
                underperformance_threshold=_normalize_underperformance_threshold(threshold),
                enabled=self.enabled,
            )
            blocked = guard.check(state)
            if blocked:
                result = guard.evaluate(state)
                return True, result.reason or "market_relative_underperformance"
            return False, ""
        blocked = super().check(state)
        if blocked:
            result = self.evaluate(state)
            return True, result.reason or "market_relative_underperformance"
        return False, ""


__all__ = [
    "MarketRelativeUnderperformanceGuard",
    "check_market_relative_underperformance",
    "should_block_entry",
]
