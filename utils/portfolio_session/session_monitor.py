"""Session monitor — track session alpha vs SPY and underperformance flag."""
from __future__ import annotations

import logging
import time
from typing import Any

from utils.portfolio_session.config import get_market_relative_underperformance_threshold
from utils.portfolio_session.metrics.session_alpha import compute_session_alpha_vs_spy

log = logging.getLogger(__name__)

_DEFAULT_REFRESH_SEC = 300
_last_refresh_monotonic = 0.0
_session_state: dict[str, Any] = {}


def _compute_alpha(session_state: dict[str, Any]) -> float | None:
    return compute_session_alpha_vs_spy(session_state)


def update_session_metrics(
    session_state: dict[str, Any] | None = None,
    *,
    force: bool = False,
    refresh_interval_sec: int = _DEFAULT_REFRESH_SEC,
) -> dict[str, Any]:
    """Refresh session alpha vs SPY; set session_underperforming when below threshold."""
    global _last_refresh_monotonic, _session_state

    now = time.monotonic()
    # Explicit session_state must always win — never serve a prior live/cache snapshot.
    if (
        session_state is None
        and not force
        and _session_state
        and refresh_interval_sec > 0
        and (now - _last_refresh_monotonic) < refresh_interval_sec
    ):
        return dict(_session_state)

    state: dict[str, Any]
    if session_state:
        state = dict(session_state)
    else:
        try:
            from utils.market_benchmark import build_portfolio_session_metrics

            port = build_portfolio_session_metrics()
            state = dict(port)
        except Exception:
            state = {"benchmark_ok": False}

    alpha = _compute_alpha(state)
    threshold = get_market_relative_underperformance_threshold()
    if alpha is not None:
        state["alpha_vs_spy_pct"] = alpha
        state["session_alpha_vs_spy"] = alpha
        underperforming = bool(state.get("benchmark_ok", True)) and alpha < threshold
        state["session_underperforming"] = underperforming
        log.info(
            "session_monitor alpha_vs_spy_pct=%.4f threshold=%.4f session_underperforming=%s",
            alpha,
            threshold,
            underperforming,
        )
    else:
        state.setdefault("session_underperforming", False)

    # Only cache live/default refreshes — not caller-provided unit-test snapshots.
    if session_state is None:
        _session_state = state
        _last_refresh_monotonic = now
    return state


def get_session_state(*, force: bool = False) -> dict[str, Any]:
    """Return cached session monitor state for gate evaluation."""
    return update_session_metrics(force=force)


def get_session_alpha_vs_spy(
    session_state: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> float | None:
    """Return current 1d session alpha vs SPY in percent points."""
    state = update_session_metrics(session_state, force=force)
    alpha = state.get("alpha_vs_spy_pct")
    if alpha is None:
        alpha = state.get("session_alpha_vs_spy")
    if alpha is None:
        return None
    try:
        return float(alpha)
    except (TypeError, ValueError):
        return None


def reset_session_monitor() -> None:
    """Clear cached monitor state (for tests)."""
    global _last_refresh_monotonic, _session_state
    _last_refresh_monotonic = 0.0
    _session_state = {}
