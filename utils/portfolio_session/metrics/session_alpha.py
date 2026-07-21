"""Session alpha vs SPY metrics for portfolio session entry guards."""
from __future__ import annotations

from typing import Any


def compute_session_alpha_vs_spy(session_context: dict[str, Any]) -> float | None:
    """Return session alpha vs SPY in percent points when derivable."""
    for key in ("alpha_vs_spy_pct", "session_alpha_vs_spy", "alpha_vs_spy"):
        raw = session_context.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

    session_return = session_context.get("session_return_pct")
    spy_return = session_context.get("spy_return_pct")
    if spy_return is None:
        spy_return = session_context.get("benchmark_change_1d_pct")
    if session_return is not None and spy_return is not None:
        try:
            return float(session_return) - float(spy_return)
        except (TypeError, ValueError):
            return None
    return None


def enrich_session_context_with_alpha(session_context: dict[str, Any]) -> dict[str, Any]:
    """Ensure session_context exposes alpha_vs_spy_pct for guard evaluation."""
    state = dict(session_context)
    alpha = compute_session_alpha_vs_spy(state)
    if alpha is not None:
        state["alpha_vs_spy_pct"] = alpha
        state["session_alpha_vs_spy"] = alpha
    return state
