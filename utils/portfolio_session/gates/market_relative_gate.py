"""Block new entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.portfolio_session.gates.base import BaseGate, GateResult

log = logging.getLogger(__name__)


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def compute_lookback_alpha(
    session_state: dict[str, Any],
    lookback_minutes: int,
    *,
    window_seconds: int | None = None,
) -> float | None:
    """Session alpha vs SPY over rolling window; None when data is insufficient."""
    if window_seconds is not None and int(window_seconds) > 0:
        window = timedelta(seconds=int(window_seconds))
    elif lookback_minutes > 0:
        window = timedelta(minutes=int(lookback_minutes))
    else:
        window = None

    direct = session_state.get("lookback_alpha_vs_spy_pct")
    if direct is not None and window is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            pass

    snapshots = session_state.get("alpha_snapshots")
    if isinstance(snapshots, list) and snapshots and window is not None:
        cutoff = datetime.now(timezone.utc) - window
        portfolio_returns: list[float] = []
        spy_returns: list[float] = []
        for row in snapshots:
            if not isinstance(row, dict):
                continue
            ts = _parse_ts(row.get("ts"))
            if ts is not None and ts < cutoff:
                continue
            try:
                portfolio_returns.append(float(row.get("portfolio_return_pct") or 0))
                spy_returns.append(float(row.get("spy_return_pct") or 0))
            except (TypeError, ValueError):
                continue
        if portfolio_returns and spy_returns and len(portfolio_returns) == len(spy_returns):
            port_delta = portfolio_returns[-1] - portfolio_returns[0]
            spy_delta = spy_returns[-1] - spy_returns[0]
            return round(port_delta - spy_delta, 4)

    if window is None:
        for key in ("session_alpha_vs_spy", "alpha_vs_spy_pct", "alpha_vs_spy"):
            alpha = session_state.get(key)
            if alpha is not None and session_state.get("benchmark_ok", True):
                try:
                    return float(alpha)
                except (TypeError, ValueError):
                    return None
        return None

    for key in ("session_alpha_vs_spy", "alpha_vs_spy_pct", "alpha_vs_spy"):
        alpha = session_state.get(key)
        if alpha is not None and session_state.get("benchmark_ok", True):
            try:
                return float(alpha)
            except (TypeError, ValueError):
                return None
    return None


class MarketRelativeGate(BaseGate):
    name = "market_relative"

    def __init__(
        self,
        *,
        max_underperformance_pct: float = -0.5,
        lookback_minutes: int = 0,
        window_seconds: int = 300,
        enabled: bool = True,
    ) -> None:
        self.max_underperformance_pct = float(max_underperformance_pct)
        self.lookback_minutes = int(lookback_minutes)
        self.window_seconds = int(window_seconds)
        self.enabled = bool(enabled)

    def evaluate(self, session_state: dict[str, Any]) -> GateResult:
        if not self.enabled:
            return GateResult(blocked=False, gate=self.name)

        if not session_state.get("benchmark_ok", True):
            return GateResult(
                blocked=False,
                gate=self.name,
                detail="benchmark_unavailable",
            )

        alpha = compute_lookback_alpha(
            session_state,
            self.lookback_minutes,
            window_seconds=self.window_seconds,
        )
        if alpha is None:
            return GateResult(
                blocked=False,
                gate=self.name,
                detail="missing_alpha_data",
            )

        exit_count = 0
        for key in ("exit_count", "session_exit_count"):
            raw = session_state.get(key)
            if raw is not None:
                try:
                    exit_count = int(raw)
                    break
                except (TypeError, ValueError):
                    exit_count = 0

        realized_usd = 0.0
        for key in ("realized_usd", "session_realized_usd"):
            raw = session_state.get(key)
            if raw is not None:
                try:
                    realized_usd = float(raw)
                    break
                except (TypeError, ValueError):
                    realized_usd = 0.0

        if alpha < self.max_underperformance_pct and exit_count == 0 and realized_usd <= 0:
            try:
                from utils.portfolio_session.constructive_tape_override import (
                    maybe_allow_despite_underperformance,
                )

                allow, ov_detail = maybe_allow_despite_underperformance(
                    float(alpha),
                    hard_threshold=float(self.max_underperformance_pct),
                    session_state=session_state,
                )
                if allow:
                    return GateResult(
                        blocked=False,
                        gate=self.name,
                        detail=ov_detail,
                        reason="constructive_tape_entry_override",
                    )
            except Exception as e:
                log.debug("constructive_tape_override failed: %s", e)

            detail = (
                f"Session underperformed SPY by {abs(alpha):.2f}% "
                f"(alpha_vs_spy_pct={alpha:.4f} threshold={self.max_underperformance_pct:.4f} "
                f"exit_count={exit_count} realized_usd={realized_usd:.2f} "
                f"window_seconds={self.window_seconds})"
            )
            log.warning("market_relative_underperformance MarketRelativeGate %s", detail)
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
