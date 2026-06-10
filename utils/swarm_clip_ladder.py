"""SI-gated clip ladder — add 2nd–5th share on clear winners (skim + infra swarms)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

_COMPONENTS = frozenset({"skim_swarm", "infra_swarm"})


def _component(component: str) -> str:
    c = (component or "skim_swarm").strip()
    return c if c in _COMPONENTS else "skim_swarm"


def clip_ladder_enabled(component: str = "skim_swarm") -> bool:
    c = _component(component)
    if c == "skim_swarm":
        key = "FORTRESS_SKIM_CLIP_LADDER"
        default = "1"
    else:
        key = "FORTRESS_INFRA_CLIP_LADDER"
        default = "1"
    global_on = str(os.environ.get("FORTRESS_SWARM_CLIP_LADDER", "")).strip().lower()
    if global_on in ("0", "false", "no", "off"):
        return False
    if global_on in ("1", "true", "yes", "on"):
        return True
    return str(os.environ.get(key, default)).strip().lower() in ("1", "true", "yes", "on")


def max_shares_per_symbol(component: str = "skim_swarm") -> int:
    if not clip_ladder_enabled(component):
        return 1
    try:
        return max(1, min(10, int(os.environ.get("FORTRESS_SWARM_MAX_SHARES_PER_SYMBOL", "5") or 5)))
    except ValueError:
        return 5


def clip_size() -> int:
    try:
        return max(1, int(os.environ.get("FORTRESS_SWARM_CLIP_SIZE", "1") or 1))
    except ValueError:
        return 1


def clip_min_gap_sec(component: str = "skim_swarm") -> float:
    for key in (
        "FORTRESS_SWARM_CLIP_MIN_GAP_SEC",
        "FORTRESS_SKIM_CLIP_MIN_GAP_SEC" if _component(component) == "skim_swarm" else "FORTRESS_INFRA_CLIP_MIN_GAP_SEC",
    ):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            try:
                return max(30.0, float(raw))
            except ValueError:
                break
    return 180.0


def clip_min_hold_sec(component: str = "skim_swarm") -> float:
    for key in (
        "FORTRESS_SWARM_CLIP_MIN_HOLD_SEC",
        "FORTRESS_SKIM_CLIP_MIN_HOLD_SEC" if _component(component) == "skim_swarm" else "FORTRESS_INFRA_CLIP_MIN_HOLD_SEC",
    ):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            try:
                return max(15.0, float(raw))
            except ValueError:
                break
    return 90.0


def _session_mode_shares_cap(mode: str, component: str) -> int:
    return {
        "normal": max_shares_per_symbol(component),
        "tight": 3,
        "churn": 2,
        "critical": 1,
    }.get(mode, 1)


def _load_learned(symbol: str, component: str) -> dict[str, Any]:
    sym = symbol.upper()
    if component == "infra_swarm":
        from agents.infra_swarm.symbol_learning import load_learned

        return load_learned(sym)
    from agents.skim_swarm.symbol_learning import load_learned

    return load_learned(sym)


def _load_symbol_state(symbol: str, component: str) -> dict[str, Any]:
    sym = symbol.upper()
    if component == "infra_swarm":
        from agents.infra_swarm.state import load_symbol_state

        return load_symbol_state(sym)
    from agents.skim_swarm.state import load_symbol_state

    return load_symbol_state(sym)


def historical_tier_max(symbol: str, component: str = "skim_swarm") -> int:
    """Per-symbol tier from session stats: 5 / 3 / 1 shares."""
    if not clip_ladder_enabled(component):
        return 1
    try:
        learned = _load_learned(symbol, component)
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


def _score_qualifies(side: str, score: float, enter_threshold: float, *, margin: float = 1.0) -> bool:
    side = side.lower()
    thr = float(enter_threshold) * float(margin)
    if side == "long":
        return float(score) >= thr
    if side == "short":
        return float(score) <= thr
    return False


def clear_winner_in_trade(
    *,
    unrealized: float,
    target_usd: float,
    score: float,
    enter_threshold: float,
    side: str,
    historical_max: int,
) -> bool:
    """True when the open trade shows winner momentum — eligible for add-clip."""
    if float(unrealized) <= 0 or float(target_usd) <= 0:
        return False
    if not _score_qualifies(side, score, enter_threshold):
        return False
    progress = float(unrealized) / float(target_usd)
    if progress >= 0.14:
        return True
    if progress >= 0.08 and int(historical_max) >= 3:
        return True
    return progress >= 0.10 and _score_qualifies(side, score, enter_threshold, margin=1.12)


def in_trade_clip_cap(
    *,
    unrealized: float,
    target_usd: float,
    score: float,
    enter_threshold: float,
    side: str,
    historical_max: int,
    component: str = "skim_swarm",
) -> int:
    """Max shares for this symbol when in-trade winner signals are present."""
    hist = max(1, int(historical_max))
    cap = max_shares_per_symbol(component)
    if not clear_winner_in_trade(
        unrealized=unrealized,
        target_usd=target_usd,
        score=score,
        enter_threshold=enter_threshold,
        side=side,
        historical_max=hist,
    ):
        return hist

    progress = float(unrealized) / float(target_usd)
    if progress >= 0.52 and _score_qualifies(side, score, enter_threshold, margin=1.12):
        in_trade = 5
    elif progress >= 0.38:
        in_trade = 4
    elif progress >= 0.26:
        in_trade = 3
    elif progress >= 0.16:
        in_trade = 2
    else:
        in_trade = 2 if hist >= 2 else max(hist, 2)

    return max(1, min(cap, max(hist, in_trade)))


def effective_max_shares(
    symbol: str,
    component: str = "skim_swarm",
    *,
    unrealized: float | None = None,
    target_usd: float | None = None,
    score: float | None = None,
    enter_threshold: float | None = None,
    side: str | None = None,
) -> int:
    """Min of env cap, historical tier, session SI mode, and in-trade clear-winner lift."""
    c = _component(component)
    if not clip_ladder_enabled(c):
        return 1
    tier = historical_tier_max(symbol, c)
    base = max_shares_per_symbol(c)
    try:
        from utils.swarm_session_si import load_session_policy

        mode = str(load_session_policy(c).get("mode") or "normal")
        mode_cap = _session_mode_shares_cap(mode, c)
    except Exception:
        mode_cap = base

    historical = max(1, min(base, tier, mode_cap))
    if unrealized is None or target_usd is None or score is None or enter_threshold is None or not side:
        return historical

    lifted = in_trade_clip_cap(
        unrealized=float(unrealized),
        target_usd=float(target_usd),
        score=float(score),
        enter_threshold=float(enter_threshold),
        side=str(side),
        historical_max=historical,
        component=c,
    )
    return max(1, min(base, mode_cap, lifted))


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
    component: str = "skim_swarm",
    side: str,
    pos_qty: int,
    unrealized: float,
    target_usd: float,
    score: float,
    enter_threshold: float,
) -> tuple[bool, str | None]:
    """Gate 2nd+ share clips on clear-winner edge, session SI, and anti-churn timing."""
    c = _component(component)
    if not clip_ladder_enabled(c):
        return False, "clip_ladder_off"
    sym = symbol.upper()
    side = side.lower()
    if pos_qty < 1:
        return False, "no_position"
    max_sh = effective_max_shares(
        sym,
        c,
        unrealized=float(unrealized),
        target_usd=float(target_usd),
        score=float(score),
        enter_threshold=float(enter_threshold),
        side=side,
    )
    if pos_qty >= max_sh:
        return False, "max_shares"
    if float(unrealized) < 0:
        return False, "clip_unrealized_negative"
    if not clear_winner_in_trade(
        unrealized=float(unrealized),
        target_usd=float(target_usd),
        score=float(score),
        enter_threshold=float(enter_threshold),
        side=side,
        historical_max=historical_tier_max(sym, c),
    ):
        return False, "not_clear_winner"
    if side == "long" and score < enter_threshold:
        return False, "clip_score_weak"
    if side == "short" and score > enter_threshold:
        return False, "clip_score_weak"

    try:
        from utils.swarm_session_si import load_session_policy

        mode = str(load_session_policy(c).get("mode") or "normal")
        if mode == "critical":
            return False, "session_si_critical"
    except Exception:
        pass

    try:
        st = _load_symbol_state(sym, c)
    except Exception:
        st = {}

    now = datetime.now(timezone.utc)
    entry_ts = _parse_ts(st.get("entry_ts"))
    if entry_ts and (now - entry_ts).total_seconds() < clip_min_hold_sec(c):
        return False, "clip_min_hold"

    last_clip = _parse_ts(st.get("last_clip_ts"))
    if last_clip and (now - last_clip).total_seconds() < clip_min_gap_sec(c):
        return False, "clip_min_gap"

    last_exit = _parse_ts(st.get("last_exit_ts"))
    if last_exit and (now - last_exit).total_seconds() < clip_min_gap_sec(c):
        return False, "clip_post_exit_gap"

    return True, None
