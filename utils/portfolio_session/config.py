"""Portfolio session configuration constants."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_FORTRESS_AI = Path(__file__).resolve().parent.parent.parent
_TRADING_BOT = Path(os.environ.get("TRADING_BOT_ROOT", "/home/ubuntu/trading-bot"))

# Default -0.5% alpha vs SPY; immutable floor -0.50% (env/yaml may tighten only).
MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD: float = -0.5
MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_PCT: float = -0.5
MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_FLOOR: float = -0.50
MARKET_RELATIVE_UNDERPERFORMANCE_ENABLED: bool = True


def threshold_to_pct_points(raw: float) -> float:
    """Convert threshold to percent points for alpha_vs_spy_pct comparison."""
    value = float(raw)
    if 0 < abs(value) < 0.1:
        return value * 100.0
    return value


def _coerce_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_market_relative_underperformance_threshold() -> float:
    """Underperformance limit in percent points (env-tunable; cannot loosen below floor)."""
    floor = threshold_to_pct_points(MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_FLOOR)
    default = threshold_to_pct_points(MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD)
    raw = os.environ.get("MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD")
    if raw is None:
        raw = os.environ.get("MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_PCT")
    if raw is None:
        raw = os.environ.get("FORTRESS_MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_PCT")
    if raw is not None:
        try:
            value = threshold_to_pct_points(float(raw))
            return max(value, floor)
        except (TypeError, ValueError):
            pass
    session_cfg = load_session_config()
    if "market_relative_underperformance_threshold" in session_cfg:
        try:
            value = threshold_to_pct_points(float(session_cfg["market_relative_underperformance_threshold"]))
            return max(value, floor)
        except (TypeError, ValueError):
            pass
    risk_guards = _load_yaml_doc(_FORTRESS_AI / "config" / "risk_guards.yaml")
    risk_section = risk_guards.get("pre_trade_gates", {}).get("market_relative_underperformance")
    if isinstance(risk_section, dict) and "threshold_alpha_pct" in risk_section:
        try:
            value = threshold_to_pct_points(float(risk_section["threshold_alpha_pct"]))
            return max(value, floor)
        except (TypeError, ValueError):
            pass
    for path in (
        _TRADING_BOT / "config" / "session_config.yaml",
        _FORTRESS_AI / "config" / "session_config.yaml",
        _TRADING_BOT / "config" / "session_guards.yaml",
        _FORTRESS_AI / "config" / "session_guards.yaml",
        _FORTRESS_AI / "config" / "portfolio_session.yaml",
    ):
        doc = _load_yaml_doc(path)
        if "market_relative_underperformance_threshold" in doc:
            try:
                value = threshold_to_pct_points(float(doc["market_relative_underperformance_threshold"]))
                return max(value, floor)
            except (TypeError, ValueError):
                pass
        for section_key in ("market_relative", "market_relative_underperformance", "market_relative_guard"):
            section = doc.get(section_key)
            if not isinstance(section, dict):
                continue
            for key in (
                "underperformance_threshold",
                "underperformance_threshold_pct",
                "market_relative_underperformance_threshold",
                "market_relative_underperformance_threshold_pct",
                "threshold_pct",
            ):
                if key in section:
                    try:
                        value = threshold_to_pct_points(float(section[key]))
                        return max(value, floor)
                    except (TypeError, ValueError):
                        break
    return max(default, floor)


def get_market_relative_underperformance_enabled() -> bool:
    """Return whether market-relative underperformance guard is active."""
    raw = os.environ.get("MARKET_RELATIVE_UNDERPERFORMANCE_ENABLED")
    if raw is not None:
        return _coerce_bool(raw, MARKET_RELATIVE_UNDERPERFORMANCE_ENABLED)
    session_cfg = load_session_config()
    if "market_relative_underperformance_enabled" in session_cfg:
        return _coerce_bool(session_cfg["market_relative_underperformance_enabled"], True)
    for path in (
        _TRADING_BOT / "config" / "session_config.yaml",
        _FORTRESS_AI / "config" / "session_config.yaml",
        _TRADING_BOT / "config" / "session_guards.yaml",
        _FORTRESS_AI / "config" / "session_guards.yaml",
        _FORTRESS_AI / "config" / "portfolio_session.yaml",
    ):
        doc = _load_yaml_doc(path)
        if "market_relative_underperformance_enabled" in doc:
            return _coerce_bool(doc["market_relative_underperformance_enabled"], True)
        section = doc.get("market_relative_underperformance")
        if isinstance(section, dict):
            if "market_relative_underperformance_enabled" in section:
                return _coerce_bool(section["market_relative_underperformance_enabled"], True)
            if "enabled" in section:
                return _coerce_bool(section["enabled"], True)
        section = doc.get("market_relative_guard")
        if isinstance(section, dict) and "enabled" in section:
            return _coerce_bool(section["enabled"], True)
        section = doc.get("market_relative")
        if isinstance(section, dict) and "enabled" in section:
            return _coerce_bool(section["enabled"], True)
    return MARKET_RELATIVE_UNDERPERFORMANCE_ENABLED


def get_market_relative_underperformance_threshold_pct() -> float:
    return get_market_relative_underperformance_threshold()


def load_market_relative_underperformance_threshold_pct() -> float:
    return get_market_relative_underperformance_threshold()


def _load_yaml_doc(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


@lru_cache(maxsize=1)
def load_session_config() -> dict[str, Any]:
    """Load portfolio session controls (beta_leaders, macro guard knobs)."""
    merged: dict[str, Any] = {
        "beta_leaders": [],
        "market_relative_underperformance_enabled": MARKET_RELATIVE_UNDERPERFORMANCE_ENABLED,
        "market_relative_underperformance_threshold": MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD,
        "market_relative_underperformance_threshold_pct": MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_PCT,
    }
    for path in (
        _TRADING_BOT / "config" / "session_config.yaml",
        _FORTRESS_AI / "config" / "session_config.yaml",
    ):
        doc = _load_yaml_doc(path)
        leaders = doc.get("beta_leaders")
        if isinstance(leaders, list):
            merged["beta_leaders"] = [str(sym).strip().upper() for sym in leaders if str(sym).strip()]
        if "market_relative_underperformance_enabled" in doc:
            merged["market_relative_underperformance_enabled"] = _coerce_bool(
                doc["market_relative_underperformance_enabled"],
                MARKET_RELATIVE_UNDERPERFORMANCE_ENABLED,
            )
        if "market_relative_underperformance_threshold" in doc:
            try:
                merged["market_relative_underperformance_threshold"] = threshold_to_pct_points(
                    float(doc["market_relative_underperformance_threshold"])
                )
            except (TypeError, ValueError):
                pass
        if "market_relative_underperformance_threshold_pct" in doc:
            try:
                merged["market_relative_underperformance_threshold_pct"] = threshold_to_pct_points(
                    float(doc["market_relative_underperformance_threshold_pct"])
                )
            except (TypeError, ValueError):
                pass
    return merged


def get_beta_leaders() -> frozenset[str]:
    """Symbols exempt from negative-alpha active session entry disable."""
    leaders = load_session_config().get("beta_leaders") or []
    return frozenset(str(sym).strip().upper() for sym in leaders if str(sym).strip())


@lru_cache(maxsize=1)
def get_market_relative_entry_block_config() -> dict[str, Any]:
    """Load entry_blocks.market_relative_underperformance from portfolio_session.yaml."""
    cfg: dict[str, Any] = {
        "enabled": True,
        "threshold": -0.005,
    }
    for path in (
        _TRADING_BOT / "config" / "portfolio_session.yaml",
        _FORTRESS_AI / "config" / "portfolio_session.yaml",
    ):
        doc = _load_yaml_doc(path)
        section = doc.get("entry_blocks")
        if not isinstance(section, dict):
            continue
        block = section.get("market_relative_underperformance")
        if not isinstance(block, dict):
            continue
        if "enabled" in block:
            cfg["enabled"] = _coerce_bool(block["enabled"], True)
        if "threshold" in block:
            try:
                cfg["threshold"] = float(block["threshold"])
            except (TypeError, ValueError):
                pass
    return cfg


@lru_cache(maxsize=1)
def load_market_relative_guard_config() -> dict[str, Any]:
    """Alias for RiskManager market-relative gate config (guard / gate naming)."""
    from utils.portfolio_session.risk_manager import load_market_relative_gate_config

    return dict(load_market_relative_gate_config())


# Back-compat alias used by entry_block_manager / deploy patches.
load_market_relative_gate_config = load_market_relative_guard_config
