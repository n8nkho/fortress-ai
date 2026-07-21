"""Session metrics for portfolio session entry guards."""
from __future__ import annotations

from typing import Any

from utils.portfolio_session.metrics.session_alpha import (
    compute_session_alpha_vs_spy,
    enrich_session_context_with_alpha,
)


def build_session_metrics(session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute and expose alpha_vs_spy_pct for guard evaluation."""
    state = dict(session_state or {})
    state.setdefault("component", "portfolio_session")
    return enrich_session_context_with_alpha(state)


__all__ = [
    "build_session_metrics",
    "compute_session_alpha_vs_spy",
    "enrich_session_context_with_alpha",
]
