"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
import time
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
_cooldown_until_monotonic: float | None = None


def should_block_entry(session_alpha_vs_spy: float, threshold: float = -0.5) -> bool:
    """Return True if session alpha vs SPY underperforms beyond threshold (percent points)."""
    return bool(check_market_relative_underperformance(session_alpha_vs_spy, threshold))


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
        cooldown_seconds: int | None = None,
        window_seconds: int | None = None,
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
        raw_cooldown = cooldown_seconds
        if raw_cooldown is None:
            raw_cooldown = cfg.get("cooldown_seconds")
        if raw_cooldown is None and window_seconds is not None:
            raw_cooldown = window_seconds
        if raw_cooldown is None:
            raw_cooldown = cfg.get("window_seconds")
        if raw_cooldown is None and cfg.get("cooldown_minutes") is not None:
            try:
                raw_cooldown = int(cfg["cooldown_minutes"]) * 60
            except (TypeError, ValueError):
                raw_cooldown = 0
        try:
            self.cooldown_seconds = max(0, int(raw_cooldown or 0))
        except (TypeError, ValueError):
            self.cooldown_seconds = 0

    @staticmethod
    def reset_cooldown() -> None:
        """Clear guard cooldown state (for tests)."""
        global _cooldown_until_monotonic
        _cooldown_until_monotonic = None

    def _cooldown_active(self) -> bool:
        global _cooldown_until_monotonic
        if self.cooldown_seconds <= 0 or _cooldown_until_monotonic is None:
            return False
        if time.monotonic() >= _cooldown_until_monotonic:
            _cooldown_until_monotonic = None
            return False
        return True

    def _arm_cooldown(self) -> None:
        global _cooldown_until_monotonic
        if self.cooldown_seconds > 0:
            _cooldown_until_monotonic = time.monotonic() + float(self.cooldown_seconds)

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
        if alpha_vs_spy >= limit:
            self.reset_cooldown()
            return GuardResult(
                blocked=False,
                guard=self.name,
                detail=f"alpha_vs_spy={alpha_vs_spy:.4f}",
            )

        try:
            from utils.portfolio_session.constructive_tape_override import (
                maybe_allow_despite_underperformance,
            )

            allow, ov_detail = maybe_allow_despite_underperformance(
                float(alpha_vs_spy),
                hard_threshold=float(limit),
                session_state=session_context,
            )
            if allow:
                return GuardResult(
                    blocked=False,
                    guard=self.name,
                    detail=ov_detail,
                    reason="constructive_tape_entry_override",
                )
        except Exception:
            pass

        if self._cooldown_active():
            detail = (
                f"session_underperforming alpha_vs_spy={alpha_vs_spy:.4f} "
                f"cooldown_seconds={self.cooldown_seconds}"
            )
            log.warning(
                "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative %s",
                detail,
            )
            return GuardResult(
                blocked=True,
                reason="market_relative_underperformance",
                detail=detail,
                guard=self.name,
            )

        detail = (
            f"session_underperforming alpha_vs_spy={alpha_vs_spy:.4f} "
            f"benchmark_change_1d_pct={benchmark_change} "
            f"market_relative_underperformance_threshold={limit:.4f} "
            f"market_relative_underperformance_threshold_bps="
            f"{int(round(self.underperformance_threshold * 100))}"
        )
        session_id = session_context.get("session_id") or session_context.get("session_key") or "?"
        self._arm_cooldown()
        log.warning(
            "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative "
            "session_id=%s %s",
            session_id,
            detail,
        )
        try:
            from utils.portfolio_session.entry_manager import record_market_relative_block

            record_market_relative_block()
        except Exception:
            pass
        return GuardResult(
            blocked=True,
            reason="market_relative_underperformance",
            detail=detail,
            guard=self.name,
        )

    def __call__(self, session_context: dict[str, Any]) -> bool:
        """Return True when entry should be blocked (alpha vs SPY below threshold)."""
        return self.evaluate(session_context).blocked

    def should_block_entry(self, session_metrics: dict[str, Any] | Any) -> str | None:
        """Return block reason when session alpha vs SPY is below threshold, else None."""
        if isinstance(session_metrics, (int, float)):
            state: dict[str, Any] = {
                "alpha_vs_spy_pct": float(session_metrics),
                "benchmark_ok": True,
            }
        elif isinstance(session_metrics, dict):
            state = session_metrics
        elif hasattr(session_metrics, "__dict__"):
            state = dict(getattr(session_metrics, "__dict__", {}))
        else:
            return None
        result = self.evaluate(state)
        if result.blocked:
            return result.reason or "market_relative_underperformance"
        return None

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
