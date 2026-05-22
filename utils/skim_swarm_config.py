"""Configuration for Fortress AI Skim Swarm (multi-symbol intraday, no LLM)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# User-facing aliases → broker/yfinance symbols
_SYMBOL_ALIASES: dict[str, str] = {
    "BRKB": "BRK.B",
    "BRK-B": "BRK.B",
    "BRK.B": "BRK.B",
}

_DEFAULT_UNIVERSE = (
    "SPY,NVDA,MSFT,GOOG,AMZN,AAPL,SOXX,NASA,"
    "BRK.B,AGIX,AVGO,LLY,V,MA,PLTR,CRWD"
)


def runtime_overrides() -> dict[str, Any]:
    p = _swarm_data_dir_path()
    path = p / "runtime_overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def runtime_denylist() -> frozenset[str]:
    """Manual operator denylist only (env or runtime_overrides.json). Never auto-populated."""
    raw = runtime_overrides().get("denylist_symbols") or []
    file_deny = frozenset(normalize_symbol(str(x)) for x in raw if str(x).strip())
    env_raw = (os.environ.get("FORTRESS_SKIM_DENYLIST") or "").strip()
    env_deny = frozenset(normalize_symbol(x) for x in env_raw.split(",") if x.strip())
    return file_deny | env_deny


def _swarm_data_dir_path() -> Path:
    raw = (os.environ.get("FORTRESS_SKIM_DATA_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
    else:
        root = Path(__file__).resolve().parent.parent
        base = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
        p = Path(base).expanduser() if base else root / "data"
        p = p / "skim_swarm"
    return p


def swarm_data_dir() -> Path:
    p = _swarm_data_dir_path()
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        return ""
    return _SYMBOL_ALIASES.get(s, s)[:16]


def universe() -> list[str]:
    raw = (os.environ.get("FORTRESS_SKIM_UNIVERSE") or _DEFAULT_UNIVERSE).strip()
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        sym = normalize_symbol(part)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out or ["SPY"]


def dry_run() -> bool:
    return str(
        os.environ.get("FORTRESS_SKIM_DRY_RUN", os.environ.get("FORTRESS_AI_DRY_RUN", "1"))
    ).strip().lower() in ("1", "true", "yes", "on")


def max_shares() -> int:
    """Hard cap: one share per symbol."""
    return 1


def base_interval_sec() -> float:
    try:
        return max(10.0, float(os.environ.get("FORTRESS_SKIM_BASE_INTERVAL_SEC", "45") or 45))
    except ValueError:
        return 45.0


def fast_interval_sec() -> float:
    try:
        return max(8.0, float(os.environ.get("FORTRESS_SKIM_FAST_INTERVAL_SEC", "12") or 12))
    except ValueError:
        return 12.0


def idle_poll_sec() -> float:
    try:
        return max(30.0, float(os.environ.get("FORTRESS_SKIM_IDLE_POLL_SEC", "60") or 60))
    except ValueError:
        return 60.0


def min_target_usd() -> float:
    try:
        return max(0.05, float(os.environ.get("FORTRESS_SKIM_MIN_TARGET_USD", "0.08") or 0.08))
    except ValueError:
        return 0.08


def min_target_pct() -> float:
    try:
        return max(0.0001, float(os.environ.get("FORTRESS_SKIM_MIN_TARGET_PCT", "0.0003") or 0.0003))
    except ValueError:
        return 0.0003


def atr_k() -> float:
    try:
        return max(0.1, float(os.environ.get("FORTRESS_SKIM_ATR_K", "0.35") or 0.35))
    except ValueError:
        return 0.35


def max_open_positions() -> int:
    try:
        return max(1, int(os.environ.get("FORTRESS_SKIM_MAX_OPEN_POSITIONS", "6") or 6))
    except ValueError:
        return 6


def slow_lane_interval_sec() -> float:
    try:
        return max(30.0, float(os.environ.get("FORTRESS_SKIM_SLOW_LANE_INTERVAL_SEC", "60") or 60))
    except ValueError:
        return 60.0


def high_vol_symbols() -> set[str]:
    """High-beta / wide-stop names — slower cadence and stricter entry gates."""
    raw = (os.environ.get("FORTRESS_SKIM_HIGH_VOL_SYMBOLS") or "LLY,CRWD,AVGO").strip()
    return {normalize_symbol(x) for x in raw.split(",") if x.strip()}


def high_vol_target_cap_usd() -> float:
    try:
        return max(0.15, float(os.environ.get("FORTRESS_SKIM_HIGH_VOL_TARGET_CAP_USD", "0.50") or 0.50))
    except ValueError:
        return 0.50


def max_stop_usd() -> float:
    try:
        return max(0.15, float(os.environ.get("FORTRESS_SKIM_MAX_STOP_USD", "0.40") or 0.40))
    except ValueError:
        return 0.40


def stop_target_mult() -> float:
    try:
        return max(1.0, float(os.environ.get("FORTRESS_SKIM_STOP_TARGET_MULT", "1.0") or 1.0))
    except ValueError:
        return 1.2


def daily_stop_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_DAILY_STOP_USD", "-200") or -200)
    except ValueError:
        return -200.0


def max_spread_bps() -> float:
    try:
        return max(5.0, float(os.environ.get("FORTRESS_SKIM_MAX_SPREAD_BPS", "25") or 25))
    except ValueError:
        return 25.0


def thin_etf_symbols() -> set[str]:
    """Wider gates / slower cadence."""
    raw = (os.environ.get("FORTRESS_SKIM_THIN_ETFS") or "NASA,AGIX,SOXX").strip()
    return {normalize_symbol(x) for x in raw.split(",") if x.strip()}


def semi_symbols() -> set[str]:
    return {normalize_symbol(x) for x in ("NVDA,MSFT,AVGO,AAPL,AMD,SOXX").split(",")}


def mega_cap_tech_symbols() -> set[str]:
    return {normalize_symbol(x) for x in ("AAPL,MSFT,GOOG,AMZN,NVDA").split(",")}


def instance_name() -> str:
    return (os.environ.get("FORTRESS_SKIM_INSTANCE_NAME") or "Fortress-Skim-Swarm").strip()


def bar_cache_ttl_sec() -> float:
    try:
        return max(5.0, float(os.environ.get("FORTRESS_SKIM_BAR_CACHE_SEC", "25") or 25))
    except ValueError:
        return 25.0


def bar_feed() -> str:
    """Alpaca data feed: iex (free, real-time) or sip (all exchanges; recent needs paid tier)."""
    return (os.environ.get("FORTRESS_SKIM_BAR_FEED") or "iex").strip().lower()


def bar_provider() -> str:
    """Bar source: auto (alpaca when creds exist), alpaca, or yfinance."""
    return (os.environ.get("FORTRESS_SKIM_BAR_PROVIDER") or "auto").strip().lower()


def improve_min_exits() -> int:
    """Minimum session exits before per-symbol param tuning runs."""
    try:
        return max(3, int(os.environ.get("FORTRESS_SKIM_IMPROVE_MIN_EXITS", "15") or 15))
    except ValueError:
        return 15


def improve_interval_exits() -> int:
    """Re-tune every N exits after improve_min_exits (15, 20, 25, ...)."""
    try:
        return max(1, int(os.environ.get("FORTRESS_SKIM_IMPROVE_INTERVAL_EXITS", "5") or 5))
    except ValueError:
        return 5


def symbol_denylist_for_unified_ai() -> frozenset[str]:
    """Symbols reserved for skim swarm — unified agent must not trade them."""
    extra = (os.environ.get("FORTRESS_AI_SYMBOL_DENYLIST") or "").strip()
    syms = set(universe())
    for part in extra.split(","):
        s = normalize_symbol(part)
        if s:
            syms.add(s)
    return frozenset(syms)
