"""Portfolio session pre-trade gate checks — market-relative underperformance."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.config import (
    get_market_relative_underperformance_enabled,
    get_market_relative_underperformance_threshold,
    load_session_config,
)
from utils.portfolio_session.gates.base import GateResult
from utils.portfolio_session.session_state import SessionState

log = logging.getLogger(__name__)


def _resolve_threshold(config: dict[str, Any] | None) -> float:
    cfg = dict(config or load_session_config())
    raw = cfg.get("market_relative_underperformance_threshold")
    if raw is None:
        raw = cfg.get("market_relative_underperformance_threshold_pct")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return get_market_relative_underperformance_threshold()


def check_market_relative_underperformance(
    session_state: SessionState | dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> GateResult:
    """Block when session alpha vs SPY is below the configured underperformance threshold."""
    if not get_market_relative_underperformance_enabled():
        return GateResult(blocked=False, gate="market_relative_underperformance")

    tracker = session_state if isinstance(session_state, SessionState) else SessionState(session_state)
    if not tracker.benchmark_ok:
        return GateResult(
            blocked=False,
            gate="market_relative_underperformance",
            detail="benchmark_unavailable",
        )

    alpha = tracker.alpha_vs_spy()
    if alpha is None:
        return GateResult(
            blocked=False,
            gate="market_relative_underperformance",
            detail="missing_alpha_data",
        )

    threshold = _resolve_threshold(config)
    if alpha >= threshold:
        return GateResult(
            blocked=False,
            gate="market_relative_underperformance",
            detail=f"alpha_vs_spy={alpha:.4f}",
        )

    try:
        from utils.portfolio_session.constructive_tape_override import (
            maybe_allow_despite_underperformance,
        )

        allow, ov_detail = maybe_allow_despite_underperformance(
            float(alpha),
            hard_threshold=float(threshold),
            session_state=tracker.as_dict(),
        )
        if allow:
            return GateResult(
                blocked=False,
                gate="market_relative_underperformance",
                detail=ov_detail,
                reason="constructive_tape_entry_override",
            )
    except Exception:
        pass

    bps = int(round(abs(threshold) * 100))
    detail = (
        f"session_underperforming alpha_vs_spy={alpha:.4f} "
        f"market_relative_underperformance_threshold={threshold:.4f} "
        f"market_relative_underperformance_threshold_bps={bps}"
    )
    log.warning(
        "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative "
        "swarm_gate_order_specific_before_macro %s",
        detail,
    )
    return GateResult(
        blocked=True,
        reason="market_relative_underperformance",
        detail=detail,
        gate="market_relative_underperformance",
    )


__all__ = ["check_market_relative_underperformance"]
