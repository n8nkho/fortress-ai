"""Portfolio session manager — macro entry guard evaluation (Classic trading-bot)."""
from __future__ import annotations

import logging
from typing import Any

from src.portfolio_session.entry_blocks import (
    _compute_session_alpha_vs_spy,
    evaluate_entry_blocks as _evaluate_entry_blocks,
)

log = logging.getLogger(__name__)


class SessionManager:
    """Track session alpha and evaluate macro entry blocks."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.entry_block_breakdown: dict[str, int] = {
            key: int(value or 0)
            for key, value in (cfg.get("entry_block_breakdown") or {}).items()
        }
        self._session_state = dict(cfg)
        self.alpha_vs_spy_pct = cfg.get("alpha_vs_spy_pct")
        if self.alpha_vs_spy_pct is None:
            self.alpha_vs_spy_pct = cfg.get("session_alpha_vs_spy")
        if self.alpha_vs_spy_pct is None:
            self.alpha_vs_spy_pct = _compute_session_alpha_vs_spy(cfg)
        self.benchmark_ok = bool(cfg.get("benchmark_ok", True))
        self.component = str(cfg.get("component") or "portfolio_session")

    def request_entry(
        self,
        entry_request: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Process a new entry request: compute session alpha vs SPY and evaluate entry blocks."""
        state = dict(self._session_state)
        if entry_request:
            state.update(entry_request)
        state.setdefault("entry_block_breakdown", dict(self.entry_block_breakdown))
        state.setdefault("benchmark_ok", self.benchmark_ok)
        state.setdefault("component", self.component)

        alpha = _compute_session_alpha_vs_spy(state)
        if alpha is not None:
            self.alpha_vs_spy_pct = alpha
            state["alpha_vs_spy_pct"] = alpha
            state["session_alpha_vs_spy"] = alpha

        blocked, reason, result_state = _evaluate_entry_blocks(
            state,
            session_alpha_vs_spy=alpha,
        )
        self.entry_block_breakdown = dict(result_state.get("entry_block_breakdown") or {})
        self._session_state = result_state
        return blocked, reason, result_state

    def evaluate_entry_blocks(self) -> tuple[bool, str, dict[str, Any]]:
        """Evaluate macro market-relative block after order-specific blocks."""
        return self.request_entry(self._session_state)


def evaluate_entry_blocks(
    session_state: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Functional wrapper for guard-engine integration."""
    return SessionManager(session_state).evaluate_entry_blocks()
