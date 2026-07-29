"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
from typing import Any

from utils.portfolio_session.config import get_market_relative_underperformance_threshold
from utils.portfolio_session.gates.base import BaseGate, GateResult
from utils.portfolio_session.gates.market_relative_gate import compute_lookback_alpha

log = logging.getLogger(__name__)


class MarketRelativeUnderperformanceGate(BaseGate):
    name = "market_relative_underperformance"

    @staticmethod
    def _try_constructive_tape_override(
        session_state: dict[str, Any],
        *,
        alpha: float,
        hard_threshold: float,
    ) -> GateResult | None:
        try:
            from utils.portfolio_session.constructive_tape_override import (
                maybe_allow_despite_underperformance,
            )

            allow, ov_detail = maybe_allow_despite_underperformance(
                float(alpha),
                hard_threshold=float(hard_threshold),
                session_state=session_state,
            )
            if allow:
                return GateResult(
                    blocked=False,
                    gate=MarketRelativeUnderperformanceGate.name,
                    detail=ov_detail,
                    reason="constructive_tape_entry_override",
                )
        except Exception as e:
            log.debug("constructive_tape_override failed: %s", e)
        return None

    def __init__(
        self,
        *,
        threshold: float | None = None,
        lookback_minutes: int = 0,
        window_seconds: int = 300,
        enabled: bool = True,
    ) -> None:
        self.threshold = float(
            threshold if threshold is not None else get_market_relative_underperformance_threshold()
        )
        self.lookback_minutes = int(lookback_minutes)
        self.window_seconds = int(window_seconds)
        self.enabled = bool(enabled)

    def evaluate(self, session_state: dict[str, Any]) -> GateResult:
        if not self.enabled:
            return GateResult(blocked=False, gate=self.name)

        if session_state.get("session_underperforming"):
            alpha_raw = session_state.get("alpha_vs_spy_pct")
            if alpha_raw is None:
                alpha_raw = session_state.get("session_alpha_vs_spy")
            if alpha_raw is not None:
                try:
                    ov = self._try_constructive_tape_override(
                        session_state,
                        alpha=float(alpha_raw),
                        hard_threshold=float(self.threshold),
                    )
                    if ov is not None:
                        return ov
                except (TypeError, ValueError):
                    pass
            detail = (
                f"session_underperforming=True alpha_vs_spy_pct="
                f"{session_state.get('alpha_vs_spy_pct')} "
                f"market_relative_underperformance_threshold_bps="
                f"{int(round(abs(self.threshold) * 100))}"
            )
            log.warning(
                "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative "
                "session_underperforming swarm_gate_order_specific_before_macro %s",
                detail,
            )
            try:
                from utils.portfolio_session.entry_manager import record_market_relative_block

                record_market_relative_block()
            except Exception:
                pass
            return GateResult(
                blocked=True,
                reason="market_relative_underperformance",
                detail=detail,
                gate=self.name,
            )

        if not session_state.get("benchmark_ok", True):
            return GateResult(blocked=False, gate=self.name, detail="benchmark_unavailable")

        alpha = compute_lookback_alpha(
            session_state,
            self.lookback_minutes,
            window_seconds=self.window_seconds,
        )
        if alpha is None:
            return GateResult(blocked=False, gate=self.name, detail="missing_alpha_data")

        threshold_bps = int(round(abs(self.threshold) * 100))
        if alpha < self.threshold:
            ov = self._try_constructive_tape_override(
                session_state,
                alpha=float(alpha),
                hard_threshold=float(self.threshold),
            )
            if ov is not None:
                return ov

            detail = (
                f"Session underperformed SPY by {abs(alpha):.2f}% "
                f"(alpha_vs_spy_pct={alpha:.4f} threshold={self.threshold:.4f} "
                f"market_relative_underperformance_threshold_bps={threshold_bps})"
            )
            log.warning(
                "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative "
                "session_underperforming swarm_gate_order_specific_before_macro %s",
                detail,
            )
            try:
                from utils.portfolio_session.entry_manager import record_market_relative_block

                record_market_relative_block()
            except Exception:
                pass
            return GateResult(
                blocked=True,
                reason="market_relative_underperformance",
                detail=detail,
                gate=self.name,
            )

        return GateResult(blocked=False, gate=self.name, detail=f"alpha_vs_spy_pct={alpha:.4f}")
