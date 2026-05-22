"""Configuration for Fortress AI Skim Swarm (multi-symbol intraday, no LLM)."""
from __future__ import annotations

import os
from pathlib import Path

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


def swarm_data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_SKIM_DATA_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
    else:
        root = Path(__file__).resolve().parent.parent
        base = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
        p = Path(base).expanduser() if base else root / "data"
        p = p / "skim_swarm"
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


def daily_stop_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_DAILY_STOP_USD", "-40") or -40)
    except ValueError:
        return -40.0


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


def symbol_denylist_for_unified_ai() -> frozenset[str]:
    """Symbols reserved for skim swarm — unified agent must not trade them."""
    extra = (os.environ.get("FORTRESS_AI_SYMBOL_DENYLIST") or "").strip()
    syms = set(universe())
    for part in extra.split(","):
        s = normalize_symbol(part)
        if s:
            syms.add(s)
    return frozenset(syms)
