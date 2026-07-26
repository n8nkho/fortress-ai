"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.guards.base import BaseGuard, GuardResult

log = logging.getLogger(__name__)

_DEFAULT_UNDERPERFORMANCE_THRESHOLD = 1.0


def _normalize_underperformance_threshold(raw: Any, default: float = _DEFAULT_UNDERPERFORMANCE_THRESHOLD) -> float:
    """Accept positive percent (0.5) or negative limit (-0.5); return positive magnitude."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value < 0:
        return abs(value)
    if 0 < value < 1:
        return value * 100.0
    return value


def _block_reason_for_alpha_vs_spy(alpha_vs_spy: float, threshold: float) -> str | None:
    """Return block reason when alpha_vs_spy underperforms threshold magnitude, else None."""
    mag = _normalize_underperformance_threshold(threshold)
    limit = -mag
    if float(alpha_vs_spy) >= limit:
        return None
    bps = int(round(mag * 100))
    detail = (
        f"session_underperforming alpha_vs_spy={float(alpha_vs_spy):.4f} "
        f"market_relative_underperformance_threshold={limit:.4f} "
        f"market_relative_underperformance_threshold_bps={bps}"
    )
    log.warning(
        "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative %s",
        detail,
    )
    return f"market_relative_underperformance {detail}"


def check_market_relative_underperformance(
    session_alpha_or_alpha_vs_spy: float | dict[str, Any],
    spy_change_or_threshold: float = -0.5,
    threshold: float | None = None,
) -> str | bool | None:
    """Block when session alpha vs SPY underperforms threshold.

    Three-arg form (SI plan): ``check_market_relative_underperformance(session_alpha, spy_change, threshold)``
    returns a block-reason string when ``session_alpha - spy_change < -threshold``, else None.

    Two-arg legacy form returns bool for precomputed alpha_vs_spy vs threshold.
    Dict form delegates to entry-guard helper and returns str | None.
    """
    if isinstance(session_alpha_or_alpha_vs_spy, dict):
        from utils.portfolio_session.entry_guards.market_relative_underperformance import (
            check_market_relative_underperformance as check_session_stats,
        )

        return check_session_stats(session_alpha_or_alpha_vs_spy, spy_change_or_threshold)

    if threshold is not None:
        # SI three-arg form: (session_return, spy_change, magnitude_pct_points).
        # Magnitude is already percent points (0.5 = 0.5%), not a 0–1 fraction.
        alpha_vs_spy = float(session_alpha_or_alpha_vs_spy) - float(spy_change_or_threshold)
        mag = abs(float(threshold))
        limit = -mag
        if float(alpha_vs_spy) >= limit:
            return None
        bps = int(round(mag * 100))
        detail = (
            f"session_underperforming alpha_vs_spy={float(alpha_vs_spy):.4f} "
            f"market_relative_underperformance_threshold={limit:.4f} "
            f"market_relative_underperformance_threshold_bps={bps}"
        )
        log.warning(
            "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative %s",
            detail,
        )
        return f"market_relative_underperformance {detail}"

    return _block_reason_for_alpha_vs_spy(
        float(session_alpha_or_alpha_vs_spy), spy_change_or_threshold
    ) is not None


def _compute_alpha_vs_spy(session_context: dict[str, Any]) -> float | None:
    for key in ("alpha_vs_spy_pct", "session_alpha_vs_spy", "alpha_vs_spy"):
        raw = session_context.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

    session = session_context.get("session")
    benchmark = session_context.get("benchmark")
    if isinstance(session, dict) and isinstance(benchmark, dict):
        alpha_1d = session.get("alpha_1d")
        if alpha_1d is None:
            alpha_1d = session.get("return_1d")
        spy_change = benchmark.get("spy_change_1d")
        if spy_change is None:
            spy_change = benchmark.get("change_1d_pct")
        if alpha_1d is not None and spy_change is not None:
            try:
                return float(alpha_1d) - float(spy_change)
            except (TypeError, ValueError):
                return None

    session_alpha = session_context.get("alpha_1d")
    if session_alpha is None:
        session_alpha = session_context.get("session_return_pct")
    spy_return = session_context.get("spy_change_1d")
    if spy_return is None:
        spy_return = session_context.get("spy_return_pct")
    if spy_return is None:
        spy_return = session_context.get("benchmark_change_1d_pct")
    if session_alpha is not None and spy_return is not None:
        try:
            return float(session_alpha) - float(spy_return)
        except (TypeError, ValueError):
            return None
    return None


class MarketRelativeGuard(BaseGuard):
    name = "market_relative"

    def __init__(
        self,
        *,
        underperformance_threshold: float | None = None,
        enabled: bool | None = None,
        config: dict[str, Any] | None = None,
        threshold_pct: float | None = None,
        threshold: float | None = None,
        **_: Any,
    ) -> None:
        cfg = dict(config or {})
        raw = underperformance_threshold
        if raw is None:
            raw = cfg.get("underperformance_threshold")
        if raw is None:
            raw = cfg.get("underperformance_threshold_pct")
        if raw is None and threshold_pct is not None:
            raw = threshold_pct
        if raw is None and threshold is not None:
            raw = threshold
        if raw is None:
            raw = cfg.get("threshold_pct", cfg.get("max_underperformance_pct"))
        self.underperformance_threshold = _normalize_underperformance_threshold(
            raw if raw is not None else _DEFAULT_UNDERPERFORMANCE_THRESHOLD
        )
        self.enabled = bool(enabled if enabled is not None else cfg.get("enabled", True))
        component = cfg.get("component")
        self.component = str(component).strip() if component else None

    def should_block(self, session_context_or_alpha: Any, threshold: float | None = None) -> bool:
        """Return True when session alpha vs SPY is below the underperformance threshold."""
        if isinstance(session_context_or_alpha, dict) or (
            threshold is None
            and not isinstance(session_context_or_alpha, (int, float))
            and hasattr(session_context_or_alpha, "alpha_vs_spy_pct")
        ):
            ctx = session_context_or_alpha
            if not isinstance(ctx, dict):
                ctx = {
                    "alpha_vs_spy_pct": getattr(session_context_or_alpha, "alpha_vs_spy_pct", None),
                    "session_alpha_vs_spy": getattr(
                        session_context_or_alpha, "session_alpha_vs_spy", None
                    ),
                    "benchmark_ok": getattr(session_context_or_alpha, "benchmark_ok", True),
                    "component": getattr(session_context_or_alpha, "component", None),
                }
            result = self.evaluate(ctx)
            return result.blocked

        if not self.enabled:
            return False
        if threshold is not None:
            limit = -_normalize_underperformance_threshold(threshold)
        else:
            limit = -self.underperformance_threshold
        return float(session_context_or_alpha) < limit

    def evaluate(self, session_context: dict[str, Any]) -> GuardResult:
        if not self.enabled:
            return GuardResult(blocked=False, guard=self.name)

        if self.component:
            session_component = session_context.get("component")
            if session_component is None:
                return GuardResult(
                    blocked=False,
                    guard=self.name,
                    detail="component_skipped",
                )
            if str(session_component).strip() != self.component:
                return GuardResult(
                    blocked=False,
                    guard=self.name,
                    detail="component_mismatch",
                )

        if not session_context.get("benchmark_ok", True):
            return GuardResult(blocked=False, guard=self.name, detail="benchmark_unavailable")

        alpha_vs_spy = _compute_alpha_vs_spy(session_context)
        if alpha_vs_spy is None:
            return GuardResult(blocked=False, guard=self.name, detail="missing_alpha_data")

        limit = -self.underperformance_threshold
        if alpha_vs_spy < limit:
            detail = (
                f"session_underperforming session underperformed SPY by {alpha_vs_spy / 100.0:.2%} "
                f"(alpha_vs_spy={alpha_vs_spy:.4f} "
                f"market_relative_underperformance_threshold={limit:.4f} "
                f"market_relative_underperformance_threshold_bps="
                f"{int(round(self.underperformance_threshold * 100))})"
            )
            log.warning("market_relative_underperformance MarketRelativeGate %s", detail)
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

    def check(self, session_context: dict[str, Any]) -> bool:
        """Return True when session alpha vs SPY is below the underperformance threshold."""
        result = self.evaluate(session_context)
        if result.blocked:
            log.warning(
                "entry_blocked_by_market_relative market_relative_underperformance "
                "MarketRelativeGate session_underperforming %s",
                result.detail or result.reason,
            )
        return result.blocked
