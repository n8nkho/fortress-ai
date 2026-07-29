"""Portfolio session entry block registry — macro blocks before swarm entries."""
from __future__ import annotations

import logging
from typing import Any, Callable

from utils.portfolio_session.config import (
    get_market_relative_underperformance_enabled,
    get_market_relative_underperformance_threshold,
    get_market_relative_entry_block_config,
    threshold_to_pct_points,
)

log = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = -0.005  # -0.5% alpha vs SPY (decimal); normalized to percent points at runtime


def market_relative_underperformance_block(
    session_alpha_vs_spy: float,
    threshold: float = _DEFAULT_THRESHOLD,
) -> bool:
    """Return True (block entry) when session alpha vs SPY is below threshold."""
    limit = threshold_to_pct_points(threshold)
    return float(session_alpha_vs_spy) < limit


def _market_relative_block_from_state(
    session_alpha_vs_spy_pct: float,
    threshold: float | None = None,
) -> bool:
    limit = float(
        threshold if threshold is not None else get_market_relative_underperformance_threshold()
    )
    return market_relative_underperformance_block(session_alpha_vs_spy_pct, limit)


def _compute_session_alpha_vs_spy(session_state: dict[str, Any]) -> float | None:
    """Return session alpha vs SPY in percent points from explicit fields or P&L vs benchmark."""
    from utils.portfolio_session.metrics.session_alpha import compute_session_alpha_vs_spy

    return compute_session_alpha_vs_spy(session_state)


def evaluate_entry_blocks(
    session_state: dict[str, Any] | None = None,
    *,
    session_alpha_vs_spy: float | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Evaluate macro market-relative block after order-specific blocks."""
    state = dict(session_state or {})
    breakdown = dict(state.get("entry_block_breakdown") or {})

    block_cfg = get_market_relative_entry_block_config()
    if not block_cfg.get("enabled", True) or not get_market_relative_underperformance_enabled():
        return False, "", {**state, "entry_block_breakdown": breakdown}

    if not state.get("benchmark_ok", True):
        return False, "", {**state, "entry_block_breakdown": breakdown}

    alpha = session_alpha_vs_spy
    if alpha is None:
        alpha = _compute_session_alpha_vs_spy(state)
    if alpha is None:
        return False, "", {**state, "entry_block_breakdown": breakdown}

    state["alpha_vs_spy_pct"] = alpha
    state["session_alpha_vs_spy"] = alpha

    threshold_raw = block_cfg.get("threshold")
    threshold = (
        threshold_to_pct_points(float(threshold_raw))
        if threshold_raw is not None
        else get_market_relative_underperformance_threshold()
    )

    # swarm_gate_order_specific_before_macro: macro gate follows per-symbol blocks
    if not market_relative_underperformance_block(alpha, threshold):
        return False, "", {**state, "entry_block_breakdown": breakdown}

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
            log.info(
                "constructive_tape_entry_override tape_override market_relative_underperformance %s",
                ov_detail,
            )
            return False, "constructive_tape_entry_override", {
                **state,
                "entry_block_breakdown": breakdown,
                "tape_override": ov_detail,
            }
    except Exception:
        pass

    breakdown["market_relative"] = int(breakdown.get("market_relative") or 0) + 1
    state["entry_block_breakdown"] = breakdown
    state["entry_block_reason"] = "market_relative_underperformance"
    bps = int(round(abs(threshold) * 100))
    detail = (
        f"session_underperforming alpha_vs_spy={alpha:.4f} "
        f"market_relative_underperformance_threshold={threshold:.4f} "
        f"market_relative_underperformance_threshold_bps={bps}"
    )
    log.warning(
        "entry_blocked_by_market_relative market_relative_underperformance "
        "MarketRelativeGate swarm_gate_order_specific_before_macro %s entry_block_breakdown=%s",
        detail,
        breakdown,
    )
    return True, "market_relative_underperformance", state


ENTRY_BLOCK_REGISTRY: dict[str, Callable[..., bool]] = {
    "market_relative_underperformance": _market_relative_block_from_state,
    "market_relative": _market_relative_block_from_state,
}

__all__ = [
    "ENTRY_BLOCK_REGISTRY",
    "evaluate_entry_blocks",
    "market_relative_underperformance_block",
]
