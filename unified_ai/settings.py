"""Load unified AI defaults from config/default.yaml with env overrides."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_YAML = _ROOT / "config" / "default.yaml"


def _coerce_value(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("true", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _parse_simple_yaml(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, val = stripped.split(":", 1)
        out[key.strip()] = _coerce_value(val)
    return out


@lru_cache(maxsize=1)
def load_defaults() -> dict[str, Any]:
    return _parse_simple_yaml(_DEFAULT_YAML)


def max_order_notional_usd(*, side: str = "SELL", portfolio_equity_usd: float | None = None) -> float:
    """Resolve max order notional from env, default.yaml, with BUY position-pct cap."""
    env_raw = (os.environ.get("FORTRESS_MAX_ORDER_NOTIONAL_USD") or "").strip()
    if env_raw:
        try:
            base = float(env_raw)
        except ValueError:
            base = 25000.0
    else:
        defaults = load_defaults()
        try:
            base = float(defaults.get("FORTRESS_MAX_ORDER_NOTIONAL_USD") or 0)
        except (TypeError, ValueError):
            base = 0.0
        if base <= 0:
            try:
                from config.defaults import FORTRESS_MAX_ORDER_NOTIONAL_USD as _cfg_cap

                base = float(_cfg_cap)
            except Exception:
                base = 50000.0

    sd = (side or "").strip().upper()
    if sd == "BUY" and portfolio_equity_usd is not None and float(portfolio_equity_usd) > 0:
        try:
            from utils.tunable_overrides import get_position_size_pct

            position_pct_cap = float(portfolio_equity_usd) * float(get_position_size_pct())
            if position_pct_cap > 0:
                return min(base, position_pct_cap)
        except Exception:
            pass
    return base


def position_deduplication_enabled() -> bool:
    env_raw = (os.environ.get("POSITION_DEDUPLICATION_ENABLED") or "").strip().lower()
    if env_raw in ("1", "true", "yes", "on"):
        return True
    if env_raw in ("0", "false", "no", "off"):
        return False
    defaults = load_defaults()
    val = defaults.get("POSITION_DEDUPLICATION_ENABLED", True)
    return bool(val)
