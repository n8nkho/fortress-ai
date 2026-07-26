"""Portfolio session state — alpha vs SPY for entry gate evaluation."""
from __future__ import annotations

from typing import Any

from utils.portfolio_session.metrics.session_alpha import enrich_session_context_with_alpha


class SessionState:
    """Session tracker snapshot used by portfolio session entry gates."""

    def __init__(self, raw: dict[str, Any] | None = None, *, refresh: bool = False) -> None:
        self._raw = dict(raw or {})
        if refresh or not self._raw:
            self._raw = self._load_from_tracker(self._raw)

    @property
    def spy_change_pct(self) -> float | None:
        """SPY session change in percent points (from market data / benchmark feed)."""
        for key in ("spy_change_pct", "spy_return_pct", "benchmark_change_1d_pct"):
            raw = self._raw.get(key)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
        return None

    @property
    def alpha_vs_spy_pct(self) -> float | None:
        """Session return % minus SPY return % (percent points)."""
        return self._resolve_alpha(enrich_session_context_with_alpha(self._raw))

    @property
    def session_alpha_vs_spy(self) -> float | None:
        """Alias for alpha_vs_spy_pct — session alpha vs SPY in percent points."""
        return self.alpha_vs_spy_pct

    def alpha_vs_spy(self) -> float | None:
        """Current session alpha vs SPY in percent points."""
        return self.alpha_vs_spy_pct

    @staticmethod
    def _resolve_alpha(state: dict[str, Any]) -> float | None:
        for key in ("alpha_vs_spy_pct", "session_alpha_vs_spy", "alpha_vs_spy"):
            raw = state.get(key)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None

        session_pnl = state.get("session_return_pct")
        if session_pnl is None:
            session_pnl = state.get("session_realized_pnl_pct")
        spy_return = state.get("spy_change_pct")
        if spy_return is None:
            spy_return = state.get("spy_return_pct")
        if spy_return is None:
            spy_return = state.get("benchmark_change_1d_pct")
        if session_pnl is not None and spy_return is not None:
            try:
                return float(session_pnl) - float(spy_return)
            except (TypeError, ValueError):
                return None
        return None

    def update_spy_change_pct(self, spy_change_pct: float) -> None:
        """Persist SPY session change from market data feed."""
        value = float(spy_change_pct)
        self._raw["spy_change_pct"] = value
        self._raw["benchmark_change_1d_pct"] = value

    def update(self, session_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge session update and persist session_alpha_vs_spy for guard evaluation."""
        if session_state:
            self._raw.update(session_state)
        spy = self.spy_change_pct
        if spy is not None:
            self._raw["spy_change_pct"] = spy
        state = enrich_session_context_with_alpha(dict(self._raw))
        alpha = self._resolve_alpha(state)
        if alpha is not None:
            self._raw["alpha_vs_spy_pct"] = alpha
            self._raw["session_alpha_vs_spy"] = alpha
        return self.as_dict()

    @property
    def benchmark_ok(self) -> bool:
        return bool(self._raw.get("benchmark_ok", True))

    def as_dict(self) -> dict[str, Any]:
        state = enrich_session_context_with_alpha(dict(self._raw))
        alpha = self.alpha_vs_spy_pct
        if alpha is not None:
            state["alpha_vs_spy_pct"] = alpha
            state["session_alpha_vs_spy"] = alpha
        return state

    @classmethod
    def _load_from_tracker(cls, base: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        try:
            from utils.market_benchmark import build_portfolio_session_metrics

            port = build_portfolio_session_metrics()
            merged.update(
                {
                    "alpha_vs_spy_pct": port.get("alpha_vs_spy_pct"),
                    "session_alpha_vs_spy": port.get("alpha_vs_spy_pct"),
                    "benchmark_ok": bool(port.get("benchmark_ok")),
                    "session_return_pct": port.get("session_return_pct"),
                    "benchmark_change_1d_pct": port.get("benchmark_change_1d_pct"),
                    "session_exit_count": port.get("session_exit_count"),
                    "session_realized_usd": port.get("session_realized_usd"),
                    "strong_tape_1d": bool(port.get("strong_tape_1d")),
                    "participation_shortfall_exits": int(port.get("participation_shortfall_exits") or 0),
                    "entry_block_breakdown": port.get("entry_block_breakdown") or {},
                }
            )
        except Exception:
            pass
        return merged


def build_session_state(session_state: dict[str, Any] | None = None, *, refresh: bool = False) -> dict[str, Any]:
    return SessionState(session_state, refresh=refresh).as_dict()
