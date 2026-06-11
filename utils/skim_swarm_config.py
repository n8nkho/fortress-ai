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
    "SPY,MSFT,GOOG,AMZN,AAPL,"
    "BRK.B,AGIX,LLY,V,MA,PLTR,CRWD"
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
    """Manual/review denylist (env, runtime_overrides.json). Never auto-populated."""
    ov = runtime_overrides()
    raw = ov.get("denylist_symbols") or []
    file_deny = frozenset(normalize_symbol(str(x)) for x in raw if str(x).strip())
    review = ov.get("review_actions") if isinstance(ov.get("review_actions"), dict) else {}
    pause_raw = review.get("pause_symbols") or ov.get("pause_symbols") or []
    pause_deny = frozenset(normalize_symbol(str(x)) for x in pause_raw if str(x).strip())
    env_raw = (os.environ.get("FORTRESS_SKIM_DENYLIST") or "").strip()
    env_deny = frozenset(normalize_symbol(x) for x in env_raw.split(",") if x.strip())
    return file_deny | pause_deny | env_deny


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
    """Shares per entry/exit clip order (not max exposure)."""
    from utils.skim_clip_ladder import clip_ladder_enabled, clip_size

    if clip_ladder_enabled():
        return clip_size()
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
    raw = (os.environ.get("FORTRESS_SKIM_HIGH_VOL_SYMBOLS") or "LLY,CRWD").strip()
    return {normalize_symbol(x) for x in raw.split(",") if x.strip()}


def high_vol_target_cap_usd() -> float:
    try:
        return max(0.15, float(os.environ.get("FORTRESS_SKIM_HIGH_VOL_TARGET_CAP_USD", "0.50") or 0.50))
    except ValueError:
        return 0.50


def max_stop_usd() -> float:
    try:
        return max(0.10, float(os.environ.get("FORTRESS_SKIM_MAX_STOP_USD", "0.30") or 0.30))
    except ValueError:
        return 0.30


def min_stop_usd() -> float:
    """Floor for stop distance — should track skim target scale, not a wide fixed $0.25."""
    raw = (os.environ.get("FORTRESS_SKIM_MIN_STOP_USD") or "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return max(min_target_usd(), 0.08)


def stop_target_mult() -> float:
    try:
        return max(0.5, float(os.environ.get("FORTRESS_SKIM_STOP_TARGET_MULT", "0.70") or 0.70))
    except ValueError:
        return 0.70


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
    raw = (os.environ.get("FORTRESS_SKIM_THIN_ETFS") or "AGIX").strip()
    return {normalize_symbol(x) for x in raw.split(",") if x.strip()}


def semi_symbols() -> set[str]:
    # Skim-owned names only — AMD/NVDA belong to the infra swarm (clear separation).
    return {normalize_symbol(x) for x in ("MSFT,AAPL").split(",")}


def mega_cap_tech_symbols() -> set[str]:
    return {normalize_symbol(x) for x in ("AAPL,MSFT,GOOG,AMZN").split(",")}


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
        return max(3, int(os.environ.get("FORTRESS_SKIM_IMPROVE_MIN_EXITS", "10") or 10))
    except ValueError:
        return 10


def improve_interval_exits() -> int:
    """Re-tune every N exits after improve_min_exits (10, 15, 20, ...)."""
    try:
        return max(1, int(os.environ.get("FORTRESS_SKIM_IMPROVE_INTERVAL_EXITS", "5") or 5))
    except ValueError:
        return 5


def side_pause_min_exits() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_SKIM_SIDE_PAUSE_MIN_EXITS", "4") or 4))
    except ValueError:
        return 4


def side_pause_min_pnl_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_SIDE_PAUSE_MIN_PNL_USD", "-0.45") or -0.45)
    except ValueError:
        return -0.45


def side_pause_share() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_SIDE_PAUSE_SHARE", "0.55") or 0.55)
    except ValueError:
        return 0.55


def symbol_pause_min_exits() -> int:
    try:
        return max(3, int(os.environ.get("FORTRESS_SKIM_SYMBOL_PAUSE_MIN_EXITS", "4") or 4))
    except ValueError:
        return 4


def symbol_pause_min_pnl_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_SYMBOL_PAUSE_MIN_PNL_USD", "-1.0") or -1.0)
    except ValueError:
        return -1.0


def symbol_pause_win_rate() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_SYMBOL_PAUSE_WIN_RATE", "0.38") or 0.38)
    except ValueError:
        return 0.38


def lifetime_pause_min_pnl_usd() -> float:
    """Pause symbol across sessions when cumulative PnL breaches this floor."""
    try:
        return float(os.environ.get("FORTRESS_SKIM_LIFETIME_PAUSE_MIN_PNL_USD", "-3.0") or -3.0)
    except ValueError:
        return -3.0


def lifetime_pause_min_exits() -> int:
    try:
        return max(3, int(os.environ.get("FORTRESS_SKIM_LIFETIME_PAUSE_MIN_EXITS", "6") or 6))
    except ValueError:
        return 6


def causation_min_samples() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_SKIM_CAUSATION_MIN_SAMPLES", "2") or 2))
    except ValueError:
        return 2


def causation_block_pnl_soft() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_CAUSATION_PNL_SOFT", "-0.35") or -0.35)
    except ValueError:
        return -0.35


def causation_block_pnl_hard() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_CAUSATION_PNL_HARD", "-0.55") or -0.55)
    except ValueError:
        return -0.55


def causation_block_win_rate() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_CAUSATION_WIN_RATE", "0.40") or 0.40)
    except ValueError:
        return 0.40


def target_winning_pattern_share() -> float:
    """Goal share of patterns with positive PnL (default 75%). Not trade win rate."""
    try:
        from utils.si_capability_review import get_capability

        cap = get_capability("winning_pattern_share_target")
        if cap is not None:
            return float(cap)
    except Exception:
        pass
    raw = (
        os.environ.get("FORTRESS_SKIM_TARGET_WINNING_PATTERN_SHARE")
        or os.environ.get("FORTRESS_SKIM_TARGET_WIN_RATE")
        or "0.75"
    )
    try:
        return float(raw)
    except ValueError:
        return 0.75


def pattern_disable_min_exits() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_SKIM_PATTERN_DISABLE_MIN_EXITS", "3") or 3))
    except ValueError:
        return 3


def _flag_on(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def continuous_si_enabled() -> bool:
    return _flag_on("FORTRESS_SKIM_CONTINUOUS_SI", "1")


def improve_every_exit() -> bool:
    return _flag_on("FORTRESS_SKIM_IMPROVE_EVERY_EXIT", "1")


def block_streak_threshold() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_SKIM_BLOCK_STREAK_THRESHOLD", "3") or 3))
    except ValueError:
        return 3


def session_expectancy_min_usd() -> float:
    try:
        return float(os.environ.get("FORTRESS_SKIM_SESSION_EXPECTANCY_MIN_USD", "-0.05") or -0.05)
    except ValueError:
        return -0.05


def shadow_lane_enabled() -> bool:
    return _flag_on("FORTRESS_SKIM_SHADOW_LANE", "1")


def shadow_promote_min_exits() -> int:
    try:
        return max(4, int(os.environ.get("FORTRESS_SKIM_SHADOW_PROMOTE_MIN_EXITS", "8") or 8))
    except ValueError:
        return 8


def autoresearch_min_winning_symbols() -> int | None:
    """Phase 2 gate — Karpathy-style autoresearch after N symbols sustain target_winning_pattern_share.

    Unset/empty env = not ready to enable autoresearch (fine-tune pattern curation first).
    Suggested starting point once live data confirms backtest: 12 of 15 universe symbols.
    """
    raw = (os.environ.get("FORTRESS_SKIM_AUTORESEARCH_MIN_WINNING_SYMBOLS") or "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def symbol_denylist_for_unified_ai() -> frozenset[str]:
    """Symbols reserved for skim swarm — unified agent must not trade them."""
    extra = (os.environ.get("FORTRESS_AI_SYMBOL_DENYLIST") or "").strip()
    syms = set(universe())
    for part in extra.split(","):
        s = normalize_symbol(part)
        if s:
            syms.add(s)
    return frozenset(syms)
