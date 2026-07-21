"""Session alpha monitor — flag active sessions underperforming SPY on uptrend tape."""
from __future__ import annotations

from typing import Any

from utils.portfolio_session.metrics.session_alpha import compute_session_alpha_vs_spy

NEGATIVE_ALPHA_ACTIVE_THRESHOLD_PCT = -0.1
MIN_EXIT_COUNT_FOR_FLAG = 3


def _resolve_exit_count(session_data: dict[str, Any]) -> int:
    for key in ("exit_count", "session_exit_count"):
        raw = session_data.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0
    return 0


def _resolve_tape_trend(session_data: dict[str, Any]) -> str:
    for key in ("tape_trend", "benchmark_tape_trend"):
        raw = session_data.get(key)
        if raw is not None:
            return str(raw).strip().lower()
    return ""


def _resolve_spy_return(spy_returns: Any) -> float | None:
    if spy_returns is None:
        return None
    if isinstance(spy_returns, (list, tuple)):
        if not spy_returns:
            return None
        raw = spy_returns[-1]
    else:
        raw = spy_returns
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def check_session_alpha(
    session_data: dict[str, Any],
    spy_returns: Any = None,
) -> dict[str, Any]:
    """Compute cumulative alpha vs SPY and flag negative-alpha active sessions."""
    state = dict(session_data or {})
    exit_count = _resolve_exit_count(state)
    tape_trend = _resolve_tape_trend(state)

    alpha = compute_session_alpha_vs_spy(state)
    if alpha is None:
        spy_return = _resolve_spy_return(spy_returns)
        session_return = state.get("session_return_pct")
        if session_return is not None and spy_return is not None:
            try:
                alpha = float(session_return) - float(spy_return)
            except (TypeError, ValueError):
                alpha = None

    negative_alpha_active_session = bool(
        exit_count >= MIN_EXIT_COUNT_FOR_FLAG
        and alpha is not None
        and float(alpha) < NEGATIVE_ALPHA_ACTIVE_THRESHOLD_PCT
        and tape_trend == "uptrend"
    )

    return {
        "negative_alpha_active_session": negative_alpha_active_session,
        "alpha_vs_spy_pct": alpha,
        "exit_count": exit_count,
        "tape_trend": tape_trend,
    }
