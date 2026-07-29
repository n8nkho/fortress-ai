"""Block swarm entries when session alpha vs SPY exceeds underperformance threshold."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from risk.guards.base import BaseGuard, GuardResult

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_RISK_GUARDS_YAML = _ROOT / "config" / "risk_guards.yaml"
_DEFAULT_THRESHOLD_ALPHA_PCT = -0.5


def _coerce_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def load_market_relative_guard_config() -> dict[str, Any]:
    """Load market_relative_underperformance guard settings from risk_guards.yaml."""
    cfg: dict[str, Any] = {
        "enabled": True,
        "threshold_alpha_pct": _DEFAULT_THRESHOLD_ALPHA_PCT,
        "check_frequency": "per_session",
    }
    if not _RISK_GUARDS_YAML.is_file():
        return cfg
    try:
        import yaml

        doc = yaml.safe_load(_RISK_GUARDS_YAML.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(doc, dict):
        return cfg
    gate_cfg = doc.get("pre_trade_gates", {}).get("market_relative_underperformance", {})
    if not isinstance(gate_cfg, dict):
        return cfg
    if "enabled" in gate_cfg:
        cfg["enabled"] = _coerce_bool(gate_cfg["enabled"], bool(cfg["enabled"]))
    if "threshold_alpha_pct" in gate_cfg:
        try:
            cfg["threshold_alpha_pct"] = float(gate_cfg["threshold_alpha_pct"])
        except (TypeError, ValueError):
            pass
    if "check_frequency" in gate_cfg:
        cfg["check_frequency"] = str(gate_cfg["check_frequency"])
    return cfg


def _alpha_from_state(session_state: dict[str, Any]) -> float | None:
    if not session_state.get("benchmark_ok", True):
        return None
    for key in ("session_alpha_vs_spy", "alpha_vs_spy_pct", "alpha_vs_spy"):
        raw = session_state.get(key)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


class MarketRelativeGuard(BaseGuard):
    """Block entries when session alpha vs SPY is below threshold_alpha_pct."""

    name = "market_relative_underperformance"

    def __init__(
        self,
        *,
        threshold_alpha_pct: float | None = None,
        threshold: float | None = None,
        enabled: bool | None = None,
        lookback_minutes: int = 0,
        config: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        cfg = {**load_market_relative_guard_config(), **(config or {})}
        raw = threshold_alpha_pct if threshold_alpha_pct is not None else threshold
        if raw is None:
            raw = cfg.get("threshold_alpha_pct", _DEFAULT_THRESHOLD_ALPHA_PCT)
        self.threshold_alpha_pct = float(raw)
        self.enabled = bool(enabled if enabled is not None else cfg.get("enabled", True))
        self.lookback_minutes = int(lookback_minutes)

    def check(self, session_alpha_vs_spy: float) -> bool:
        """Return True (block) if session alpha vs SPY is below threshold_alpha_pct."""
        if not self.enabled:
            return False
        alpha = float(session_alpha_vs_spy)
        if alpha < self.threshold_alpha_pct:
            threshold_bps = int(round(abs(self.threshold_alpha_pct) * 100))
            log.warning(
                "market_relative_underperformance MarketRelativeGate entry_blocked_by_market_relative "
                "session_underperforming alpha_vs_spy=%.4f "
                "market_relative_underperformance_threshold=%.4f "
                "market_relative_underperformance_threshold_bps=%s",
                alpha,
                self.threshold_alpha_pct,
                threshold_bps,
            )
            return True
        return False

    def should_block(
        self,
        session_alpha_vs_spy: float | dict[str, Any],
        threshold: float | None = None,
    ) -> bool:
        if isinstance(session_alpha_vs_spy, dict):
            return self.evaluate(session_alpha_vs_spy).blocked
        limit = float(threshold) if threshold is not None else self.threshold_alpha_pct
        return float(session_alpha_vs_spy) < limit

    def evaluate(self, session_state: dict[str, Any] | float) -> GuardResult:
        if isinstance(session_state, (int, float)):
            blocked = self.check(float(session_state))
            if blocked:
                return GuardResult(
                    blocked=True,
                    reason="market_relative_underperformance",
                    detail=(
                        f"session_underperforming alpha_vs_spy={float(session_state):.4f} "
                        f"market_relative_underperformance_threshold={self.threshold_alpha_pct:.4f}"
                    ),
                    guard=self.name,
                )
            return GuardResult(blocked=False, guard=self.name)

        alpha = _alpha_from_state(session_state)
        if alpha is None:
            return GuardResult(blocked=False, guard=self.name, detail="missing_alpha_data")
        if self.check(alpha):
            return GuardResult(
                blocked=True,
                reason="market_relative_underperformance",
                detail=(
                    f"session_underperforming alpha_vs_spy={alpha:.4f} "
                    f"market_relative_underperformance_threshold={self.threshold_alpha_pct:.4f} "
                    f"market_relative_underperformance_threshold_bps="
                    f"{int(round(abs(self.threshold_alpha_pct) * 100))}"
                ),
                guard=self.name,
            )
        return GuardResult(blocked=False, guard=self.name, detail=f"alpha_vs_spy={alpha:.4f}")


MarketRelativeUnderperformanceGuard = MarketRelativeGuard

__all__ = [
    "MarketRelativeGuard",
    "MarketRelativeUnderperformanceGuard",
    "load_market_relative_guard_config",
]
