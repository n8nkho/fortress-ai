"""Portfolio session risk manager — evaluates pre-trade gates before entry blocks."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from utils.portfolio_session.gates import PRE_TRADE_GATES, BaseGate, GateResult
from utils.portfolio_session.gates.market_relative_gate import MarketRelativeGate
from utils.portfolio_session.gates.market_relative_underperformance_gate import (
    MarketRelativeUnderperformanceGate,
)
from utils.portfolio_session.session_monitor import reset_session_monitor, update_session_metrics

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_PORTFOLIO_SESSION_YAML = _ROOT / "config" / "portfolio_session.yaml"
_SESSION_GUARDS_YAML = _ROOT / "config" / "session_guards.yaml"
_GUARD_CONFIG = Path(__file__).resolve().parent / "config" / "guard_config.yaml"
_DEFAULT_GUARDS_YAML = Path(__file__).resolve().parent / "config" / "default_guards.yaml"
_GUARDS_YAML_LOCAL = Path(__file__).resolve().parent / "config" / "guards.yaml"
_GUARDS_YAML = _ROOT / "config" / "guards.yaml"
_last_market_relative_block_ts: datetime | None = None


def _coerce_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _coerce_float(raw: Any, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _coerce_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _load_yaml_section(path: Path, *keys: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(doc, dict):
        return {}
    node: Any = doc
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _normalize_underperformance_threshold(raw: Any, default: float = -1.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value > 0:
        return -value * 100.0
    return value


def _parse_lookback_period(raw: Any) -> int:
    """Map lookback_period ('1d', '5m', …) to lookback_minutes; session/1d → 0 (session alpha)."""
    text = str(raw or "").strip().lower()
    if not text or text in ("session", "1d", "day"):
        return 0
    try:
        if text.endswith("m") and text[:-1].isdigit():
            return int(text[:-1])
        if text.endswith("h") and text[:-1].isdigit():
            return int(text[:-1]) * 60
        if text.endswith("d") and text[:-1].isdigit():
            return int(text[:-1]) * 1440
    except (TypeError, ValueError):
        return 0
    return 0


def _bps_to_pct(raw: Any) -> float | None:
    """Convert basis points to percentage points (e.g. -50 bps -> -0.5%)."""
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return None


def _apply_guard_yaml_section(cfg: dict[str, Any], guard_cfg: dict[str, Any]) -> None:
    if "enabled" in guard_cfg:
        cfg["enabled"] = guard_cfg["enabled"]
    if "market_relative_underperformance_threshold_pct" in guard_cfg:
        cfg["threshold_pct"] = guard_cfg["market_relative_underperformance_threshold_pct"]
        cfg["max_underperformance_pct"] = guard_cfg["market_relative_underperformance_threshold_pct"]
        cfg["underperformance_threshold_pct"] = guard_cfg["market_relative_underperformance_threshold_pct"]
        cfg["market_relative_underperformance_threshold"] = guard_cfg[
            "market_relative_underperformance_threshold_pct"
        ]
    if "market_relative_underperformance_threshold_bps" in guard_cfg:
        bps_pct = _bps_to_pct(guard_cfg["market_relative_underperformance_threshold_bps"])
        if bps_pct is not None:
            cfg["market_relative_underperformance_threshold_bps"] = guard_cfg[
                "market_relative_underperformance_threshold_bps"
            ]
            cfg["threshold_pct"] = bps_pct
            cfg["max_underperformance_pct"] = bps_pct
            cfg["underperformance_threshold_pct"] = bps_pct
    if "threshold" in guard_cfg:
        cfg["max_underperformance_pct"] = _normalize_underperformance_threshold(guard_cfg["threshold"])
    if "threshold_pct" in guard_cfg:
        cfg["threshold_pct"] = guard_cfg["threshold_pct"]
        cfg["max_underperformance_pct"] = guard_cfg["threshold_pct"]
    if "underperformance_threshold_pct" in guard_cfg:
        cfg["threshold_pct"] = guard_cfg["underperformance_threshold_pct"]
        cfg["max_underperformance_pct"] = guard_cfg["underperformance_threshold_pct"]
    if "threshold_alpha_pp" in guard_cfg:
        cfg["threshold_alpha_pp"] = guard_cfg["threshold_alpha_pp"]
        cfg["threshold_pct"] = guard_cfg["threshold_alpha_pp"]
        cfg["max_underperformance_pct"] = guard_cfg["threshold_alpha_pp"]
        cfg["underperformance_threshold_pct"] = guard_cfg["threshold_alpha_pp"]
        cfg["market_relative_underperformance_threshold"] = guard_cfg["threshold_alpha_pp"]
        cfg["market_relative_underperformance_threshold_bps"] = int(
            round(abs(float(guard_cfg["threshold_alpha_pp"])) * 100)
        )
    if "threshold_alpha_vs_spy_pct" in guard_cfg:
        cfg["threshold_alpha_vs_spy_pct"] = guard_cfg["threshold_alpha_vs_spy_pct"]
        cfg["threshold_pct"] = guard_cfg["threshold_alpha_vs_spy_pct"]
        cfg["max_underperformance_pct"] = guard_cfg["threshold_alpha_vs_spy_pct"]
    if "underperformance_threshold" in guard_cfg:
        raw = _coerce_float(guard_cfg["underperformance_threshold"], 0.5)
        limit = -abs(raw)
        cfg["underperformance_threshold"] = raw
        cfg["threshold_pct"] = limit
        cfg["max_underperformance_pct"] = limit
        cfg["underperformance_threshold_pct"] = limit
        cfg["market_relative_underperformance_threshold"] = limit
        cfg["market_relative_underperformance_threshold_bps"] = int(round(abs(raw) * 100))
    if "threshold_alpha_underperformance_pct" in guard_cfg:
        raw = _coerce_float(guard_cfg["threshold_alpha_underperformance_pct"], 0.5)
        limit = -abs(raw)
        cfg["threshold_alpha_underperformance_pct"] = raw
        cfg["threshold_pct"] = limit
        cfg["max_underperformance_pct"] = limit
        cfg["underperformance_threshold_pct"] = limit
        cfg["market_relative_underperformance_threshold"] = limit
    if "threshold_percent" in guard_cfg:
        cfg["threshold_pct"] = guard_cfg["threshold_percent"]
        cfg["max_underperformance_pct"] = guard_cfg["threshold_percent"]
        cfg["market_relative_underperformance_threshold"] = guard_cfg["threshold_percent"]
    if "market_relative_underperformance_threshold" in guard_cfg:
        cfg["market_relative_underperformance_threshold"] = guard_cfg[
            "market_relative_underperformance_threshold"
        ]
        cfg["threshold_pct"] = guard_cfg["market_relative_underperformance_threshold"]
        cfg["max_underperformance_pct"] = guard_cfg["market_relative_underperformance_threshold"]
    if "max_underperformance_pct" in guard_cfg:
        cfg["max_underperformance_pct"] = guard_cfg["max_underperformance_pct"]
    for key in ("lookback_minutes", "cooldown_seconds", "window_seconds"):
        if key in guard_cfg:
            cfg[key] = guard_cfg[key]
    if "lookback_period" in guard_cfg:
        cfg["lookback_minutes"] = _parse_lookback_period(guard_cfg["lookback_period"])
    if "cooldown_minutes" in guard_cfg:
        cfg["cooldown_seconds"] = _coerce_int(guard_cfg["cooldown_minutes"], 60) * 60
    if "component" in guard_cfg:
        cfg["component"] = guard_cfg["component"]


def load_market_relative_gate_config() -> dict[str, Any]:
    """Resolve gate config from env, fortress default.yaml, and optional runtime YAML."""
    from utils.portfolio_session.config import get_market_relative_underperformance_threshold

    default_threshold = get_market_relative_underperformance_threshold()
    default_bps = int(round(abs(default_threshold) * 100))
    cfg: dict[str, Any] = {
        "enabled": True,
        "threshold_pct": default_threshold,
        "max_underperformance_pct": default_threshold,
        "underperformance_threshold_pct": default_threshold,
        "market_relative_underperformance_threshold_pct": default_threshold,
        "market_relative_underperformance_threshold_bps": -default_bps,
        "window_seconds": 300,
        "lookback_minutes": 0,
        "cooldown_seconds": 300,
    }

    defaults_yaml = _ROOT / "config" / "defaults.yaml"
    if defaults_yaml.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(defaults_yaml, "portfolio_session", "guards"))
        _apply_guard_yaml_section(
            cfg,
            _load_yaml_section(
                defaults_yaml, "portfolio_session", "guards", "market_relative_underperformance"
            ),
        )

    trading_bot_defaults = _ROOT / "config" / "trading_bot_defaults.yaml"
    if trading_bot_defaults.is_file():
        tb_guard = _load_yaml_section(
            trading_bot_defaults, "guards", "market_relative_underperformance"
        )
        if "threshold_percent" in tb_guard:
            tb_guard = {**tb_guard, "threshold_pct": tb_guard["threshold_percent"]}
        _apply_guard_yaml_section(cfg, tb_guard)

    if _PORTFOLIO_SESSION_YAML.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(_PORTFOLIO_SESSION_YAML, "market_relative"))
        _apply_guard_yaml_section(cfg, _load_yaml_section(_PORTFOLIO_SESSION_YAML, "guards", "market_relative"))
        _apply_guard_yaml_section(
            cfg, _load_yaml_section(_PORTFOLIO_SESSION_YAML, "market_relative_underperformance")
        )
        _apply_guard_yaml_section(cfg, _load_yaml_section(_PORTFOLIO_SESSION_YAML, "market_relative_guard"))

    if _GUARDS_YAML.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(_GUARDS_YAML, "market_relative_underperformance"))
        _apply_guard_yaml_section(cfg, _load_yaml_section(_GUARDS_YAML, "market_relative"))

    if _GUARDS_YAML_LOCAL.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(_GUARDS_YAML_LOCAL, "market_relative_underperformance"))
        _apply_guard_yaml_section(cfg, _load_yaml_section(_GUARDS_YAML_LOCAL, "market_relative"))

    if _DEFAULT_GUARDS_YAML.is_file():
        _apply_guard_yaml_section(
            cfg, _load_yaml_section(_DEFAULT_GUARDS_YAML, "market_relative_underperformance")
        )
        _apply_guard_yaml_section(cfg, _load_yaml_section(_DEFAULT_GUARDS_YAML, "market_relative"))

    _DEFAULT_PORTFOLIO_SESSION_YAML = Path(__file__).resolve().parent / "config" / "default.yaml"
    if _DEFAULT_PORTFOLIO_SESSION_YAML.is_file():
        _apply_guard_yaml_section(
            cfg,
            _load_yaml_section(
                _DEFAULT_PORTFOLIO_SESSION_YAML, "guards", "market_relative_underperformance"
            ),
        )
        _apply_guard_yaml_section(
            cfg, _load_yaml_section(_DEFAULT_PORTFOLIO_SESSION_YAML, "guards", "market_relative")
        )

    if _GUARD_CONFIG.is_file():
        guard_cfg = _load_yaml_section(
            _GUARD_CONFIG, "entry_guards", "market_relative_underperformance"
        )
        if not guard_cfg:
            guard_cfg = _load_yaml_section(_GUARD_CONFIG, "market_relative_underperformance")
        if guard_cfg:
            _apply_guard_yaml_section(cfg, guard_cfg)

    risk_guards_yaml = _ROOT / "config" / "risk_guards.yaml"
    if risk_guards_yaml.is_file():
        gate_cfg = _load_yaml_section(
            risk_guards_yaml, "pre_trade_gates", "market_relative_underperformance"
        )
        if gate_cfg:
            if "threshold_alpha_pct" in gate_cfg:
                gate_cfg = {**gate_cfg, "threshold_pct": gate_cfg["threshold_alpha_pct"]}
            _apply_guard_yaml_section(cfg, gate_cfg)

    # session_guards.yaml wins over generic guards.yaml (SI default -0.5pp).
    if _SESSION_GUARDS_YAML.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(_SESSION_GUARDS_YAML, "market_relative"))
        _apply_guard_yaml_section(
            cfg, _load_yaml_section(_SESSION_GUARDS_YAML, "market_relative_underperformance")
        )
        _apply_guard_yaml_section(
            cfg,
            _load_yaml_section(
                _SESSION_GUARDS_YAML, "entry_guards", "market_relative_underperformance"
            ),
        )

    trading_bot_session_guards = _ROOT.parent / "trading-bot" / "config" / "session_guards.yaml"
    if trading_bot_session_guards.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(trading_bot_session_guards, "market_relative"))
        _apply_guard_yaml_section(
            cfg,
            _load_yaml_section(trading_bot_session_guards, "market_relative_underperformance"),
        )

    trading_bot_default = _ROOT.parent / "trading-bot" / "config" / "default.yaml"
    if trading_bot_default.is_file():
        _apply_guard_yaml_section(cfg, _load_yaml_section(trading_bot_default, "market_relative_guard"))
        _apply_guard_yaml_section(
            cfg,
            _load_yaml_section(trading_bot_default, "portfolio_session", "guards", "market_relative_underperformance"),
        )
        yaml_cfg = _load_yaml_section(trading_bot_default, "risk", "market_relative_gate")
        for key in ("enabled", "max_underperformance_pct", "threshold_pct", "lookback_minutes", "cooldown_seconds", "window_seconds"):
            if key in yaml_cfg:
                cfg[key] = yaml_cfg[key]

    runtime_path = (os.environ.get("FORTRESS_RUNTIME_CONFIG") or "").strip()
    if runtime_path:
        yaml_cfg = _load_yaml_section(Path(runtime_path), "risk", "market_relative_gate")
    else:
        sibling = _ROOT.parent / "trading-bot" / "config" / "default.yaml"
        fortress_runtime = _ROOT.parent / "trading-bot" / "config" / "fortress_runtime.yaml"
        yaml_cfg = _load_yaml_section(sibling, "risk", "market_relative_gate")
        if not yaml_cfg:
            yaml_cfg = _load_yaml_section(fortress_runtime, "risk", "market_relative_gate")

    for key in ("enabled", "max_underperformance_pct", "threshold_pct", "lookback_minutes", "cooldown_seconds", "window_seconds"):
        if key in yaml_cfg:
            cfg[key] = yaml_cfg[key]

    defaults_yaml = _ROOT / "config" / "default.yaml"
    if defaults_yaml.is_file():
        flat: dict[str, Any] = {}
        for line in defaults_yaml.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, val = stripped.split(":", 1)
            flat[key.strip()] = val.strip()
        if "MARKET_RELATIVE_GATE_ENABLED" in flat:
            cfg["enabled"] = _coerce_bool(flat["MARKET_RELATIVE_GATE_ENABLED"], bool(cfg["enabled"]))
        if "MARKET_RELATIVE_GATE_MAX_UNDERPERFORMANCE_PCT" in flat:
            cfg["max_underperformance_pct"] = _coerce_float(
                flat["MARKET_RELATIVE_GATE_MAX_UNDERPERFORMANCE_PCT"],
                float(cfg["max_underperformance_pct"]),
            )
        if "MARKET_RELATIVE_GATE_LOOKBACK_MINUTES" in flat:
            cfg["lookback_minutes"] = _coerce_int(
                flat["MARKET_RELATIVE_GATE_LOOKBACK_MINUTES"],
                int(cfg["lookback_minutes"]),
            )
        if "MARKET_RELATIVE_GATE_COOLDOWN_SECONDS" in flat:
            cfg["cooldown_seconds"] = _coerce_int(
                flat["MARKET_RELATIVE_GATE_COOLDOWN_SECONDS"],
                int(cfg["cooldown_seconds"]),
            )

    env_enabled = (os.environ.get("FORTRESS_MARKET_RELATIVE_GATE_ENABLED") or "").strip()
    if env_enabled:
        cfg["enabled"] = _coerce_bool(env_enabled, bool(cfg["enabled"]))
    env_max = (os.environ.get("FORTRESS_MARKET_RELATIVE_GATE_MAX_UNDERPERFORMANCE_PCT") or "").strip()
    if env_max:
        cfg["max_underperformance_pct"] = _coerce_float(env_max, float(cfg["max_underperformance_pct"]))
    env_lookback = (os.environ.get("FORTRESS_MARKET_RELATIVE_GATE_LOOKBACK_MINUTES") or "").strip()
    if env_lookback:
        cfg["lookback_minutes"] = _coerce_int(env_lookback, int(cfg["lookback_minutes"]))
    env_cooldown = (os.environ.get("FORTRESS_MARKET_RELATIVE_GATE_COOLDOWN_SECONDS") or "").strip()
    if env_cooldown:
        cfg["cooldown_seconds"] = _coerce_int(env_cooldown, int(cfg["cooldown_seconds"]))

    cfg["enabled"] = _coerce_bool(cfg.get("enabled"), True)
    cfg["max_underperformance_pct"] = _coerce_float(cfg.get("max_underperformance_pct"), default_threshold)
    cfg["threshold_pct"] = _coerce_float(
        cfg.get("threshold_pct", cfg.get("underperformance_threshold_pct", cfg["max_underperformance_pct"])),
        default_threshold,
    )
    cfg["underperformance_threshold_pct"] = _coerce_float(
        cfg.get("underperformance_threshold_pct", cfg["threshold_pct"]),
        default_threshold,
    )
    # Immutable floor: cannot loosen below configured cap via yaml/env.
    threshold_pct = float(
        cfg.get("threshold_pct", cfg.get("underperformance_threshold_pct", cfg["max_underperformance_pct"]))
    )
    cfg["threshold_pct"] = max(threshold_pct, default_threshold)
    cfg["underperformance_threshold_pct"] = max(
        float(cfg.get("underperformance_threshold_pct", cfg["threshold_pct"])),
        default_threshold,
    )
    cfg["max_underperformance_pct"] = cfg["threshold_pct"]
    cfg["market_relative_underperformance_threshold"] = cfg["threshold_pct"]
    cfg["window_seconds"] = _coerce_int(cfg.get("window_seconds"), 300)
    cfg["lookback_minutes"] = _coerce_int(cfg.get("lookback_minutes"), 0)
    cfg["cooldown_seconds"] = _coerce_int(cfg.get("cooldown_seconds"), 300)
    return cfg


# Alias used by entry_guard_router / entry_block_manager.
load_market_relative_guard_config = load_market_relative_gate_config


def _build_pre_trade_gates() -> list[BaseGate]:
    cfg = load_market_relative_gate_config()
    threshold = float(
        cfg.get("market_relative_underperformance_threshold", cfg.get("threshold_pct", -0.5))
    )
    lookback_minutes = int(cfg.get("lookback_minutes", 0))
    window_seconds = int(cfg.get("window_seconds", 300))
    enabled = bool(cfg.get("enabled", True))
    gates: list[BaseGate] = []
    for gate_cls in PRE_TRADE_GATES:
        if gate_cls is MarketRelativeGate:
            gates.append(
                MarketRelativeGate(
                    max_underperformance_pct=threshold,
                    lookback_minutes=lookback_minutes,
                    window_seconds=window_seconds,
                    enabled=enabled,
                )
            )
        else:
            gates.append(
                MarketRelativeUnderperformanceGate(
                    threshold=threshold,
                    lookback_minutes=lookback_minutes,
                    window_seconds=window_seconds,
                    enabled=enabled,
                )
            )
    return gates


def _entry_gate_from_gates(gates: list[BaseGate]) -> "EntryGate | None":
    from utils.portfolio_session.entry_gate import EntryGate
    from utils.portfolio_session.guards.market_relative import MarketRelativeGuard

    guards: list[MarketRelativeGuard] = []
    for gate in gates:
        if isinstance(gate, MarketRelativeUnderperformanceGate):
            guards.append(
                MarketRelativeGuard(
                    underperformance_threshold=abs(float(gate.threshold)),
                    enabled=gate.enabled,
                )
            )
        elif isinstance(gate, MarketRelativeGate):
            guards.append(
                MarketRelativeGuard(
                    underperformance_threshold=abs(float(gate.max_underperformance_pct)),
                    enabled=gate.enabled,
                )
            )
    return EntryGate(guards=guards) if guards else None


def build_session_state(*, session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    if session_state:
        return dict(session_state)
    from utils.market_benchmark import build_portfolio_session_metrics

    port = build_portfolio_session_metrics()
    alpha = port.get("alpha_vs_spy_pct")
    return {
        "component": "portfolio_session",
        "alpha_vs_spy_pct": alpha,
        "session_alpha_vs_spy": alpha,
        "benchmark_ok": bool(port.get("benchmark_ok")),
        "benchmark_change_1d_pct": port.get("benchmark_change_1d_pct"),
        "session_return_pct": port.get("session_return_pct"),
        "session_realized_usd": port.get("session_realized_usd"),
        "session_exit_count": port.get("session_exit_count"),
        "strong_tape_1d": bool(port.get("strong_tape_1d")),
        "participation_shortfall_exits": int(port.get("participation_shortfall_exits") or 0),
        "entry_block_breakdown": port.get("entry_block_breakdown") or {},
    }


class RiskManager:
    def __init__(
        self,
        gates: list[BaseGate] | None = None,
        *,
        cooldown_seconds: int | None = None,
    ) -> None:
        self._gates = gates if gates is not None else _build_pre_trade_gates()
        self._entry_gate = _entry_gate_from_gates(gates) if gates is not None else None
        if cooldown_seconds is None:
            cooldown_seconds = int(load_market_relative_gate_config().get("cooldown_seconds") or 0)
        self._cooldown_seconds = max(0, int(cooldown_seconds))

    def evaluate_pre_trade_gates(self, session_state: dict[str, Any] | None = None) -> GateResult:
        global _last_market_relative_block_ts

        if self._cooldown_seconds > 0 and _last_market_relative_block_ts is not None:
            elapsed = datetime.now(timezone.utc) - _last_market_relative_block_ts
            if elapsed < timedelta(seconds=self._cooldown_seconds):
                detail = f"cooldown_seconds={self._cooldown_seconds} elapsed={int(elapsed.total_seconds())}"
                log.info("entry_blocked_by_market_relative %s", detail)
                return GateResult(
                    blocked=True,
                    reason="market_relative_underperformance",
                    detail=detail,
                    gate="market_relative",
                )

        state = update_session_metrics(build_session_state(session_state=session_state))
        for gate in self._gates:
            result = gate.evaluate(state)
            if result.blocked:
                _last_market_relative_block_ts = datetime.now(timezone.utc)
                log.info("entry_blocked_by_market_relative %s", result.detail or result.reason)
                return GateResult(
                    blocked=True,
                    reason=result.reason or "market_relative_underperformance",
                    detail=result.detail,
                    gate=result.gate or "market_relative",
                )
        return GateResult(blocked=False)


def reset_market_relative_cooldown() -> None:
    """Clear cooldown state (for tests)."""
    global _last_market_relative_block_ts
    _last_market_relative_block_ts = None
    reset_session_monitor()
    from utils.portfolio_session.guards.market_relative_underperformance import (
        MarketRelativeUnderperformanceGuard,
    )

    MarketRelativeUnderperformanceGuard.reset_cooldown()
    get_risk_manager.cache_clear()
    from utils.portfolio_session.entry_gate import get_entry_gate

    get_entry_gate.cache_clear()


@lru_cache(maxsize=1)
def get_risk_manager() -> RiskManager:
    return RiskManager()


def entry_blocked_by_market_relative(session_state: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Return (blocked, block_reason) for swarm signal integration."""
    result = get_risk_manager().evaluate_pre_trade_gates(session_state=session_state)
    if result.blocked:
        return True, result.reason or "market_relative_underperformance"
    return False, ""
