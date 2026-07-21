"""Portfolio session entry guard helpers."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.config import (
    get_market_relative_underperformance_enabled,
    get_market_relative_underperformance_threshold,
)
from utils.portfolio_session.entry_guards.market_relative_underperformance import (
    check_market_relative_underperformance,
)
from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha
from utils.portfolio_session.session_monitor import update_session_metrics
from utils.portfolio_session.session_state import SessionState

log = logging.getLogger(__name__)


def market_relative_underperformance_gate(
    session_alpha_vs_spy: float | None,
    threshold: float = -0.5,
) -> bool:
    """Return True (block entry) when session alpha vs SPY is below threshold (percent points)."""
    if session_alpha_vs_spy is None:
        return False
    return float(session_alpha_vs_spy) < float(threshold)


def evaluate_entry_blocks(
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate macro entry guards; market-relative underperformance runs first."""
    state = update_session_metrics(
        enrich_session_context_with_alpha(dict(session_state or {}))
    )
    state.setdefault("component", "portfolio_session")

    if not get_market_relative_underperformance_enabled():
        return {
            "blocked": False,
            "reason": "",
            "market_relative_underperformance": False,
        }

    if not state.get("benchmark_ok", True):
        return {
            "blocked": False,
            "reason": "",
            "market_relative_underperformance": False,
        }

    tracker = SessionState(state)
    alpha = tracker.session_alpha_vs_spy
    threshold = get_market_relative_underperformance_threshold()

    if alpha is None:
        return {
            "blocked": False,
            "reason": "",
            "market_relative_underperformance": False,
        }

    if not market_relative_underperformance_gate(alpha, threshold):
        return {
            "blocked": False,
            "reason": "",
            "market_relative_underperformance": False,
        }

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
            return {
                "blocked": False,
                "reason": "constructive_tape_entry_override",
                "market_relative_underperformance": False,
                "detail": ov_detail,
            }
    except Exception:
        pass

    bps = int(round(abs(threshold) * 100))
    breakdown = dict(state.get("entry_block_breakdown") or {})
    breakdown["market_relative"] = int(breakdown.get("market_relative") or 0) + 1
    detail = (
        f"session_underperforming alpha_vs_spy={alpha:.4f} "
        f"market_relative_underperformance_threshold={threshold:.4f} "
        f"market_relative_underperformance_threshold_bps={bps}"
    )
    log.warning(
        "entry_blocked_by_market_relative market_relative_underperformance "
        "MarketRelativeGate %s swarm_gate_order_specific_before_macro",
        detail,
    )
    return {
        "blocked": True,
        "reason": "market_relative_underperformance",
        "market_relative_underperformance": True,
        "entry_block_breakdown": breakdown,
        "detail": detail,
    }


__all__ = [
    "check_market_relative_underperformance",
    "evaluate_entry_blocks",
    "market_relative_underperformance_gate",
]
