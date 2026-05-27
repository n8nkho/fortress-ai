"""SI-gated clip ladder — up to N shares per symbol, 1 share per authorized clip."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


def clip_ladder_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SKIM_CLIP_LADDER", "0")).strip().lower() in ("1", "true", "yes", "on")


def max_shares_per_symbol() -> int:
    if not clip_ladder_enabled():
        return 1
    try:
        return max(1, min(10, int(os.environ.get("FORTRESS_SKIM_MAX_SHARES_PER_SYMBOL", "5") or 5)))
    except ValueError:
        return 5


def clip_size() -> int:
    try:
        return max(1, int(os.environ.get("FORTRESS_SKIM_CLIP_SIZE", "1") or 1))
    except ValueError:
        return 1


def clip_min_gap_sec() -> float:
    try:
        return max(30.0, float(os.environ.get("FORTRESS_SKIM_CLIP_MIN_GAP_SEC", "180") or 180))
    except ValueError:
        return 180.0


def clip_min_hold_sec() -> float:
    try:
        return max(15.0, float(os.environ.get("FORTRESS_SKIM_CLIP_MIN_HOLD_SEC", "90") or 90))
    except ValueError:
        return 90.0


def _session_mode_shares_cap(mode: str) -> int:
    return {"normal": max_shares_per_symbol(), "tight": 3, "churn": 2, "critical": 1}.get(mode, 1)


def symbol_tier_max(symbol: str) -> int:
    """Per-symbol tier from session stats: 5 / 3 / 1 shares."""
    if not clip_ladder_enabled():
        return 1
    try:
        from agents.skim_swarm.symbol_learning import load_learned

        learned = load_learned(symbol.upper())
    except Exception:
        return 1
    params = learned.get("params") or {}
    if params.get("pause_entries"):
        return 1
    ss = learned.get("session_stats") or {}
    exits = int(ss.get("exits") or 0)
    wins = int(ss.get("wins") or 0)
    losses = int(ss.get("losses") or 0)
    closed = wins + losses
    wr = (wins / closed) if closed else None
    pnl = float(ss.get("sum_pnl_usd") or 0)
    exp = (pnl / exits) if exits else None
    if exits >= 3 and wr is not None and wr >= 0.45 and exp is not None and exp >= 0:
        return 5
    if exits >= 2 and wr is not None and wr >= 0.38 and exp is not None and exp >= -0.02:
        return 3
    return 1


def effective_max_shares(symbol: str) -> int:
    """Min of env cap, symbol tier, and swarm session SI mode."""
    if not clip_ladder_enabled():
        return 1
    tier = symbol_tier_max(symbol)
    base = max_shares_per_symbol()
    try:
        from utils.swarm_session_si import load_session_policy

        mode = str(load_session_policy("skim_swarm").get("mode") or "normal")
        mode_cap = _session_mode_shares_cap(mode)
    except Exception:
        mode_cap = base
    return max(1, min(base, tier, mode_cap))


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def authorize_add_clip(
    symbol: str,
    *,
    side: str,
    pos_qty: int,
    unrealized: float,
    score: float,
    enter_threshold: float,
) -> tuple[bool, str | None]:
    """Gate 2nd+ share clips on edge, session SI, and anti-churn timing."""
    if not clip_ladder_enabled():
        return False, "clip_ladder_off"
    sym = symbol.upper()
    side = side.lower()
    if pos_qty < 1:
        return False, "no_position"
    max_sh = effective_max_shares(sym)
    if pos_qty >= max_sh:
        return False, "max_shares"
    if float(unrealized) < 0:
        return False, "clip_unrealized_negative"
    if side == "long" and score < enter_threshold:
        return False, "clip_score_weak"
    if side == "short" and score > enter_threshold:
        return False, "clip_score_weak"

    try:
        from utils.swarm_session_si import load_session_policy

        mode = str(load_session_policy("skim_swarm").get("mode") or "normal")
        if mode == "critical":
            return False, "session_si_critical"
    except Exception:
        pass

    try:
        from agents.skim_swarm.state import load_symbol_state

        st = load_symbol_state(sym)
    except Exception:
        st = {}

    now = datetime.now(timezone.utc)
    entry_ts = _parse_ts(st.get("entry_ts"))
    if entry_ts and (now - entry_ts).total_seconds() < clip_min_hold_sec():
        return False, "clip_min_hold"

    last_clip = _parse_ts(st.get("last_clip_ts"))
    if last_clip and (now - last_clip).total_seconds() < clip_min_gap_sec():
        return False, "clip_min_gap"

    last_exit = _parse_ts(st.get("last_exit_ts"))
    if last_exit and (now - last_exit).total_seconds() < clip_min_gap_sec():
        return False, "clip_post_exit_gap"

    return True, None
