"""Per-symbol continuous intraday self-improvement (RTH hot loop)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from utils.skim_swarm_config import (
    block_streak_threshold,
    continuous_si_enabled,
    session_expectancy_min_usd,
    shadow_lane_enabled,
    shadow_promote_min_exits,
    stop_target_mult,
)

_DEFAULT_OVERLAY = {
    "enter_long_delta_boost": 0.0,
    "enter_short_delta_boost": 0.0,
    "target_mult_overlay": 1.0,
    "stop_mult_overlay": 1.0,
    "spread_bps_mult": 1.0,
}

_DEFAULT_STREAKS = {
    "stop_loss": 0,
    "target_hit": 0,
    "last_pattern": None,
}

_DEFAULT_SHADOW = {
    "variant": "tighter_stop",
    "target_mult_delta": -0.08,
    "live_pnl_usd": 0.0,
    "shadow_pnl_usd": 0.0,
    "live_exits": 0,
    "shadow_exits": 0,
}

_PARAM_BOUNDS = {
    "enter_long_delta": (-0.15, 0.15),
    "enter_short_delta": (-0.15, 0.15),
    "target_mult": (0.70, 1.35),
    "cooldown_mult": (0.5, 2.5),
}


def _clamp_param(name: str, val: float) -> float:
    lo, hi = _PARAM_BOUNDS.get(name, (-1.0, 1.0))
    return max(lo, min(hi, val))


def ensure_intraday_state(learned: dict[str, Any]) -> None:
    learned.setdefault("session_overlay", dict(_DEFAULT_OVERLAY))
    learned.setdefault("block_streaks", {})
    learned.setdefault("recent_exit_streak", dict(_DEFAULT_STREAKS))
    learned.setdefault("shadow", dict(_DEFAULT_SHADOW))
    learned.setdefault("adaptation_log", [])


def reset_intraday_session_state(learned: dict[str, Any]) -> None:
    """Clear per-session overlay/streaks; keep params + lifetime + causation."""
    learned["session_overlay"] = dict(_DEFAULT_OVERLAY)
    learned["block_streaks"] = {}
    learned["recent_exit_streak"] = dict(_DEFAULT_STREAKS)
    sh = learned.get("shadow") if isinstance(learned.get("shadow"), dict) else {}
    learned["shadow"] = {
        **_DEFAULT_SHADOW,
        "variant": sh.get("variant") or _DEFAULT_SHADOW["variant"],
        "target_mult_delta": float(sh.get("target_mult_delta") or _DEFAULT_SHADOW["target_mult_delta"]),
    }


def session_expectancy(stats: dict[str, Any]) -> float | None:
    exits = int(stats.get("exits") or 0)
    if exits <= 0:
        return None
    return float(stats.get("sum_pnl_usd") or 0) / exits


def session_expectancy_ok(stats: dict[str, Any]) -> bool:
    exp = session_expectancy(stats)
    if exp is None:
        return True
    return exp >= session_expectancy_min_usd()


def merge_overlay_into_params(params: dict[str, Any], learned: dict[str, Any]) -> dict[str, float]:
    """Effective numeric knobs for decide() — per symbol."""
    ensure_intraday_state(learned)
    ov = learned.get("session_overlay") or {}
    tm = float(params.get("target_mult") or 1.0) * float(ov.get("target_mult_overlay") or 1.0)
    stop_ratio = stop_target_mult() * float(ov.get("stop_mult_overlay") or 1.0)
    el_boost = float(ov.get("enter_long_delta_boost") or 0.0)
    es_boost = float(ov.get("enter_short_delta_boost") or 0.0)
    return {
        "target_mult_effective": round(_clamp_param("target_mult", tm), 4),
        "stop_target_mult_effective": round(max(0.45, min(1.0, stop_ratio)), 4),
        "enter_long_delta_boost": round(_clamp_param("enter_long_delta", el_boost), 4),
        "enter_short_delta_boost": round(_clamp_param("enter_short_delta", es_boost), 4),
        "spread_bps_mult": max(0.8, min(1.5, float(ov.get("spread_bps_mult") or 1.0))),
    }


def _block_reason_key(raw: str | None) -> str:
    if not raw:
        return "unknown"
    s = str(raw).strip().lower()
    if s.startswith("no_entry"):
        return "no_entry"
    if s.startswith("hold_"):
        return "hold"
    if s.startswith("spread"):
        return "spread_too_wide"
    if s.startswith("pattern_disabled"):
        return "pattern_disabled"
    if s.startswith("stop_loss"):
        return "stop_loss"
    if "skim_target_hit" in s or s.startswith("skim_target"):
        return "target_hit"
    return s.split(":")[0].split()[0][:32]


def record_block_event(learned: dict[str, Any], block_reason: str | None) -> None:
    if not continuous_si_enabled():
        return
    ensure_intraday_state(learned)
    key = _block_reason_key(block_reason)
    streaks = learned.setdefault("block_streaks", {})
    streaks[key] = int(streaks.get(key) or 0) + 1


def record_exit_streak(learned: dict[str, Any], *, exit_reasoning: str, pattern: str | None) -> None:
    ensure_intraday_state(learned)
    rs = learned.setdefault("recent_exit_streak", dict(_DEFAULT_STREAKS))
    key = _block_reason_key(exit_reasoning)
    if key == "stop_loss":
        rs["stop_loss"] = int(rs.get("stop_loss") or 0) + 1
        rs["target_hit"] = 0
    elif key == "target_hit":
        rs["target_hit"] = int(rs.get("target_hit") or 0) + 1
        rs["stop_loss"] = 0
    else:
        rs["stop_loss"] = 0
        rs["target_hit"] = 0
    if pattern:
        rs["last_pattern"] = pattern


def adapt_from_block_streaks(learned: dict[str, Any], params: dict[str, Any], notes: list[str]) -> None:
    """Per-symbol reaction to repeated blocks during RTH."""
    if not continuous_si_enabled():
        return
    ensure_intraday_state(learned)
    threshold = block_streak_threshold()
    streaks = learned.get("block_streaks") or {}
    ov = learned.setdefault("session_overlay", dict(_DEFAULT_OVERLAY))

    no_entry = int(streaks.get("no_entry") or 0)
    if no_entry >= threshold and no_entry % threshold == 0:
        ov["enter_long_delta_boost"] = round(
            _clamp_param("enter_long_delta", float(ov.get("enter_long_delta_boost") or 0) - 0.01),
            4,
        )
        ov["enter_short_delta_boost"] = round(
            _clamp_param("enter_short_delta", float(ov.get("enter_short_delta_boost") or 0) + 0.01),
            4,
        )
        notes.append(f"block_no_entry_loosen delta_boost L={ov['enter_long_delta_boost']}")

    spread_n = int(streaks.get("spread_too_wide") or 0)
    if spread_n >= threshold and spread_n % threshold == 0:
        ov["spread_bps_mult"] = round(min(1.35, float(ov.get("spread_bps_mult") or 1.0) * 1.05), 4)
        notes.append(f"block_spread_widen mult={ov['spread_bps_mult']}")

    pat_n = int(streaks.get("pattern_disabled") or 0)
    if pat_n >= threshold * 2:
        ov["enter_long_delta_boost"] = round(
            _clamp_param("enter_long_delta", float(ov.get("enter_long_delta_boost") or 0) + 0.015),
            4,
        )
        ov["enter_short_delta_boost"] = round(
            _clamp_param("enter_short_delta", float(ov.get("enter_short_delta_boost") or 0) - 0.015),
            4,
        )
        notes.append("block_pattern_disabled_tighten_entries")


def adapt_session_overlay(learned: dict[str, Any], notes: list[str]) -> None:
    """Wave/session controller — per symbol, from its own session stats."""
    if not continuous_si_enabled():
        return
    stats = learned.get("session_stats") or {}
    exp = session_expectancy(stats)
    if exp is None:
        return
    ov = learned.setdefault("session_overlay", dict(_DEFAULT_OVERLAY))
    exits = int(stats.get("exits") or 0)
    if exp < session_expectancy_min_usd() and exits >= 3:
        ov["enter_long_delta_boost"] = round(
            _clamp_param("enter_long_delta", float(ov.get("enter_long_delta_boost") or 0) + 0.01),
            4,
        )
        ov["enter_short_delta_boost"] = round(
            _clamp_param("enter_short_delta", float(ov.get("enter_short_delta_boost") or 0) - 0.01),
            4,
        )
        ov["stop_mult_overlay"] = round(max(0.85, float(ov.get("stop_mult_overlay") or 1.0) * 0.98), 4)
        notes.append(f"overlay_tighten exp={exp:.3f} exits={exits}")
    elif exp > 0.03 and exits >= 4:
        ov["stop_mult_overlay"] = round(min(1.05, float(ov.get("stop_mult_overlay") or 1.0) * 1.01), 4)
        notes.append(f"overlay_loosen_winner exp={exp:.3f}")


def adapt_last_exit_micro(
    learned: dict[str, Any],
    params: dict[str, Any],
    *,
    exit_reasoning: str,
    pnl_usd: float,
    pattern: str | None,
    notes: list[str],
) -> None:
    """Immediate per-exit micro adaptation — runs every closed trade."""
    if not continuous_si_enabled():
        return
    record_exit_streak(learned, exit_reasoning=exit_reasoning, pattern=pattern)
    rs = learned.get("recent_exit_streak") or {}
    ov = learned.setdefault("session_overlay", dict(_DEFAULT_OVERLAY))

    key = _block_reason_key(exit_reasoning)
    if key == "stop_loss" or pnl_usd < 0:
        params["target_mult"] = round(_clamp_param("target_mult", float(params.get("target_mult") or 1) * 0.99), 4)
        params["cooldown_mult"] = round(_clamp_param("cooldown_mult", float(params.get("cooldown_mult") or 1) * 1.02), 4)
        ov["stop_mult_overlay"] = round(max(0.82, float(ov.get("stop_mult_overlay") or 1.0) * 0.99), 4)
        notes.append(f"micro_stop_loss pnl={pnl_usd:.3f}")
        if int(rs.get("stop_loss") or 0) >= 3 and pattern:
            disabled = set(params.get("disable_patterns") or [])
            if pattern not in disabled:
                disabled.add(pattern)
                params["disable_patterns"] = sorted(disabled)
                notes.append(f"micro_disable_pattern:{pattern}")
    elif key == "target_hit" and pnl_usd > 0:
        params["cooldown_mult"] = round(_clamp_param("cooldown_mult", float(params.get("cooldown_mult") or 1) * 0.99), 4)
        notes.append(f"micro_target_hit pnl={pnl_usd:.3f}")


def append_adaptation_log(learned: dict[str, Any], notes: list[str]) -> None:
    if not notes:
        return
    log = learned.setdefault("adaptation_log", [])
    log.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "notes": notes[:12],
        }
    )
    learned["adaptation_log"] = log[-40:]


def shadow_target_mult(params: dict[str, Any], learned: dict[str, Any]) -> float:
    ensure_intraday_state(learned)
    sh = learned.get("shadow") or {}
    delta = float(sh.get("target_mult_delta") or -0.08)
    base = float(params.get("target_mult") or 1.0)
    return round(_clamp_param("target_mult", base + delta), 4)


def estimate_shadow_pnl(pnl_usd: float, exit_reasoning: str) -> float:
    """Counterfactual PnL for tighter-stop shadow variant on this symbol."""
    key = _block_reason_key(exit_reasoning)
    if key == "stop_loss" and pnl_usd < 0:
        return round(pnl_usd * 0.72, 4)
    if key == "target_hit" and pnl_usd > 0:
        return round(pnl_usd * 0.92, 4)
    return round(pnl_usd, 4)


def record_shadow_exit(learned: dict[str, Any], *, pnl_usd: float, shadow_pnl_usd: float) -> None:
    if not shadow_lane_enabled():
        return
    ensure_intraday_state(learned)
    sh = learned.setdefault("shadow", dict(_DEFAULT_SHADOW))
    sh["live_pnl_usd"] = round(float(sh.get("live_pnl_usd") or 0) + pnl_usd, 4)
    sh["shadow_pnl_usd"] = round(float(sh.get("shadow_pnl_usd") or 0) + shadow_pnl_usd, 4)
    sh["live_exits"] = int(sh.get("live_exits") or 0) + 1
    sh["shadow_exits"] = int(sh.get("shadow_exits") or 0) + 1


def maybe_promote_shadow_variant(learned: dict[str, Any], params: dict[str, Any], notes: list[str]) -> None:
    """Promote shadow param variant when shadow expectancy beats live on this symbol."""
    if not shadow_lane_enabled():
        return
    sh = learned.get("shadow") or {}
    live_ex = int(sh.get("live_exits") or 0)
    if live_ex < shadow_promote_min_exits():
        return
    live_pnl = float(sh.get("live_pnl_usd") or 0)
    shadow_pnl = float(sh.get("shadow_pnl_usd") or 0)
    live_exp = live_pnl / max(live_ex, 1)
    shadow_exp = shadow_pnl / max(live_ex, 1)
    if shadow_exp <= live_exp + 0.02:
        return
    delta = float(sh.get("target_mult_delta") or -0.08)
    params["target_mult"] = round(_clamp_param("target_mult", float(params.get("target_mult") or 1) + delta), 4)
    sh["live_pnl_usd"] = 0.0
    sh["shadow_pnl_usd"] = 0.0
    sh["live_exits"] = 0
    sh["shadow_exits"] = 0
    notes.append(f"shadow_promote target_mult={params['target_mult']:.3f}")


def log_shadow_decision(symbol: str, record: dict[str, Any]) -> None:
    if not shadow_lane_enabled():
        return
    from agents.skim_swarm.symbol_learning import experience_path

    p = experience_path(symbol).with_name(experience_path(symbol).stem + "_shadow.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **record}, default=str) + "\n")
