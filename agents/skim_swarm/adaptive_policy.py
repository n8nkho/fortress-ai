"""Per-symbol recursive self-improvement — all tunables adapt from this symbol's own stats.

Phase 1 (current): maximize winning-pattern share (positive PnL per pattern), not trade win rate.
Phase 2 (deferred): Karpathy-style autoresearch after FORTRESS_SKIM_AUTORESEARCH_MIN_WINNING_SYMBOLS
symbols sustain FORTRESS_SKIM_TARGET_WINNING_PATTERN_SHARE — threshold TBD from live paper data.
See docs/SKIM_SWARM.md.
"""
from __future__ import annotations

import json
from typing import Any

from agents.skim_swarm.symbol_causation import ensure_causation
from utils.skim_swarm_config import (
    improve_min_exits,
    pattern_disable_min_exits,
    side_pause_min_exits,
    side_pause_min_pnl_usd,
    side_pause_share,
    symbol_pause_min_exits,
    symbol_pause_min_pnl_usd,
    symbol_pause_win_rate,
    target_winning_pattern_share,
)

_BOUNDS = {
    "enter_long_delta": (-0.15, 0.15),
    "enter_short_delta": (-0.15, 0.15),
    "target_mult": (0.70, 1.35),
    "cooldown_mult": (0.5, 2.5),
    "score_bias": (-0.15, 0.15),
    "short_spy_filter": (0.0, 0.002),
    "pattern_delta": (-0.10, 0.10),
}

_PATTERNS = ("rip_fade", "pullback_uptrend", "momentum_long", "momentum_short")


def winning_pattern_share(
    pattern_stats: dict[str, Any],
    *,
    min_exits: int = 2,
    disabled: set[str] | None = None,
) -> float | None:
    """Share of enabled patterns (with min_exits) that have positive session PnL."""
    skip = disabled or set()
    scored: list[bool] = []
    for pattern in _PATTERNS:
        if pattern in skip:
            continue
        ps = pattern_stats.get(pattern) or {}
        ex = int(ps.get("exits") or 0)
        if ex < min_exits:
            continue
        scored.append(float(ps.get("sum_pnl_usd") or 0) > 0)
    if not scored:
        return None
    return sum(scored) / len(scored)


def clamp_param(name: str, val: float) -> float:
    lo, hi = _BOUNDS.get(name, (-1.0, 1.0))
    return max(lo, min(hi, val))


def _side_win_rate(stats: dict[str, Any], side: str) -> float | None:
    if side == "long":
        w, l = int(stats.get("long_wins") or 0), int(stats.get("long_losses") or 0)
    else:
        w, l = int(stats.get("short_wins") or 0), int(stats.get("short_losses") or 0)
    closed = w + l
    if closed == 0:
        return None
    return w / closed


def _adapt_side_pauses(params: dict[str, Any], stats: dict[str, Any], notes: list[str]) -> None:
    """Pause/unpause long or short for this symbol only from side-specific session stats."""
    total_ex = int(stats.get("exits") or 0)
    if total_ex < side_pause_min_exits():
        return

    min_ex = side_pause_min_exits()
    min_pnl = side_pause_min_pnl_usd()
    share_min = side_pause_share()

    for side, pause_key in (("long", "pause_long"), ("short", "pause_short")):
        if side == "long":
            ex = int(stats.get("long_exits") or 0)
            pnl = float(stats.get("long_pnl_usd") or 0)
        else:
            ex = int(stats.get("short_exits") or 0)
            pnl = float(stats.get("short_pnl_usd") or 0)

        wr = _side_win_rate(stats, side)
        share = ex / max(total_ex, 1)
        currently = bool(params.get(pause_key))

        if ex >= min_ex and pnl <= min_pnl:
            per_trade = pnl / max(ex, 1)
            toxic = per_trade <= -0.07 or (share >= share_min and pnl < 0)
            if toxic and (wr is None or wr < 0.48):
                if not currently:
                    params[pause_key] = True
                    notes.append(f"auto_{pause_key} ex={ex} pnl={pnl:.2f} wr={wr}")
                continue

        if currently and ex >= min_ex:
            if pnl > 0.12 or (wr is not None and wr >= 0.55 and pnl > -0.15):
                params[pause_key] = False
                notes.append(f"auto_unpause_{side} pnl={pnl:.2f} wr={wr}")


def _adapt_symbol_pause(params: dict[str, Any], stats: dict[str, Any], notes: list[str]) -> None:
    """Full entry pause for this symbol when session expectancy is clearly negative."""
    exits = int(stats.get("exits") or 0)
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    closed = max(wins + losses, 1)
    win_rate = wins / closed
    sum_pnl = float(stats.get("sum_pnl_usd") or 0)
    currently = bool(params.get("pause_entries"))

    if exits >= symbol_pause_min_exits() and sum_pnl <= symbol_pause_min_pnl_usd() and win_rate < symbol_pause_win_rate():
        if not currently:
            params["pause_entries"] = True
            notes.append(f"auto_pause_entries wr={win_rate:.2f} pnl={sum_pnl:.2f}")
        return

    if currently and exits >= symbol_pause_min_exits() and (sum_pnl > -0.35 or win_rate >= 0.52):
        params["pause_entries"] = False
        notes.append(f"auto_unpause_entries wr={win_rate:.2f} pnl={sum_pnl:.2f}")


def _adapt_entry_thresholds(params: dict[str, Any], stats: dict[str, Any], notes: list[str]) -> None:
    el = float(params.get("enter_long_delta") or 0)
    es = float(params.get("enter_short_delta") or 0)
    long_ex = int(stats.get("long_exits") or 0)
    short_ex = int(stats.get("short_exits") or 0)
    long_pnl = float(stats.get("long_pnl_usd") or 0)
    short_pnl = float(stats.get("short_pnl_usd") or 0)

    if long_ex >= 3 and long_pnl < -0.50:
        el += 0.02
        notes.append(f"tighten_long pnl={long_pnl:.2f}")
    elif long_ex >= 4 and long_pnl > 0.35:
        el -= 0.015
        notes.append(f"loosen_long pnl={long_pnl:.2f}")

    if short_ex >= 3 and short_pnl < -0.50:
        es += 0.02
        notes.append(f"tighten_short pnl={short_pnl:.2f}")
    elif short_ex >= 4 and short_pnl > 0.35:
        es -= 0.015
        notes.append(f"loosen_short pnl={short_pnl:.2f}")

    params["enter_long_delta"] = round(clamp_param("enter_long_delta", el), 4)
    params["enter_short_delta"] = round(clamp_param("enter_short_delta", es), 4)


def _adapt_pattern_deltas(params: dict[str, Any], learned: dict[str, Any], notes: list[str]) -> None:
    pd = dict(params.get("pattern_deltas") or {})
    for pattern, ps in (learned.get("pattern_stats") or {}).items():
        p_ex = int(ps.get("exits") or 0)
        p_pnl = float(ps.get("sum_pnl_usd") or 0)
        if p_ex < 2:
            continue
        p_wr = int(ps.get("wins") or 0) / max(p_ex, 1)
        cur = float(pd.get(pattern) or 0)
        if p_pnl < -0.35 or p_wr < 0.35:
            pd[pattern] = round(clamp_param("pattern_delta", cur + 0.025), 4)
            notes.append(f"tighten_{pattern} pnl={p_pnl:.2f}")
        elif p_pnl > 0.25 and p_wr > 0.55:
            pd[pattern] = round(clamp_param("pattern_delta", cur - 0.015), 4)
            notes.append(f"loosen_{pattern} pnl={p_pnl:.2f}")

    causation = ensure_causation(learned)
    for key in causation.get("eliminated_keys") or []:
        parts = str(key).split("|")
        if not parts:
            continue
        pat = parts[0]
        if pat in pd:
            pd[pat] = round(clamp_param("pattern_delta", float(pd.get(pat) or 0) + 0.03), 4)
            notes.append(f"causation_eliminated:{key[:48]}")

    params["pattern_deltas"] = pd


def _historical_seed_disables(learned: dict[str, Any]) -> set[str]:
    return set(learned.get("historical_seed_disables") or [])


def _adapt_disable_patterns(params: dict[str, Any], learned: dict[str, Any], notes: list[str]) -> None:
    """Hard-disable toxic patterns; re-enable only with live recovery (historical disables need stronger proof)."""
    disabled = set(params.get("disable_patterns") or [])
    seed_disables = _historical_seed_disables(learned)
    min_ex = pattern_disable_min_exits()
    for pattern, ps in (learned.get("pattern_stats") or {}).items():
        if pattern not in _PATTERNS:
            continue
        p_ex = int(ps.get("exits") or 0)
        if p_ex < min_ex:
            continue
        p_wr = int(ps.get("wins") or 0) / max(p_ex, 1)
        p_pnl = float(ps.get("sum_pnl_usd") or 0)
        if p_pnl <= -0.10:
            if pattern not in disabled:
                disabled.add(pattern)
                notes.append(f"auto_disable_{pattern} pnl={p_pnl:.2f}")
        elif pattern in disabled and p_ex >= min_ex + 2 and p_pnl > 0.20:
            if pattern in seed_disables and (p_pnl <= 0.35 or p_wr < 0.55):
                continue
            disabled.discard(pattern)
            notes.append(f"auto_enable_{pattern} pnl={p_pnl:.2f}")
    params["disable_patterns"] = sorted(disabled)


def _adapt_to_winning_pattern_share(
    params: dict[str, Any], learned: dict[str, Any], notes: list[str]
) -> None:
    """Disable losing patterns until share of winning patterns meets goal."""
    goal = target_winning_pattern_share()
    pstats = learned.get("pattern_stats") or {}
    disabled = set(params.get("disable_patterns") or [])
    share = winning_pattern_share(
        pstats, min_exits=pattern_disable_min_exits(), disabled=disabled
    )
    if share is None or share >= goal - 0.02:
        return

    disabled = set(params.get("disable_patterns") or [])
    losers: list[tuple[str, float, int]] = []
    for pattern in _PATTERNS:
        if pattern in disabled:
            continue
        ps = pstats.get(pattern) or {}
        ex = int(ps.get("exits") or 0)
        if ex < pattern_disable_min_exits():
            continue
        pnl = float(ps.get("sum_pnl_usd") or 0)
        if pnl <= 0:
            losers.append((pattern, pnl, ex))
    if not losers:
        return

    losers.sort(key=lambda x: x[1])
    worst = losers[0][0]
    disabled.add(worst)
    params["disable_patterns"] = sorted(disabled)
    notes.append(f"disable_loser_for_pattern_share:{worst} share={share:.2f} goal={goal:.2f}")


def _adapt_targets_and_cooldown(params: dict[str, Any], stats: dict[str, Any], notes: list[str]) -> None:
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    closed = max(wins + losses, 1)
    win_rate = wins / closed
    sum_pnl = float(stats.get("sum_pnl_usd") or 0)
    exits = int(stats.get("exits") or 0)

    tm = float(params.get("target_mult") or 1)
    cm = float(params.get("cooldown_mult") or 1)
    sb = float(params.get("score_bias") or 0)

    if win_rate < 0.38:
        tm *= 0.94
        cm *= 1.08
        notes.append(f"shrink_targets win_rate={win_rate:.2f}")
    elif win_rate >= 0.52 and sum_pnl < 0 and exits >= 6:
        tm *= 1.06
        notes.append(f"widen_targets_rr_fix wr={win_rate:.2f} pnl={sum_pnl:.2f}")
    elif win_rate > 0.58 and sum_pnl > 0.20:
        tm *= 1.04
        cm *= 0.96
        notes.append(f"loosen_winner wr={win_rate:.2f} pnl={sum_pnl:.2f}")

    if losses >= 3 and sum_pnl < 0:
        cm *= 1.08
        sb *= 0.6
        notes.append("loss_streak_cooldown")

    params["target_mult"] = round(clamp_param("target_mult", tm), 4)
    params["cooldown_mult"] = round(clamp_param("cooldown_mult", cm), 4)
    params["score_bias"] = round(clamp_param("score_bias", sb), 4)


def _adapt_short_spy_filter(
    symbol: str,
    params: dict[str, Any],
    stats: dict[str, Any],
    experience_path_fn,
    notes: list[str],
) -> None:
    """Learn SPY filter for shorts from this symbol's own short exits."""
    ssf = float(params.get("short_spy_filter") or 0)
    short_ex = int(stats.get("short_exits") or 0)
    short_pnl = float(stats.get("short_pnl_usd") or 0)
    short_wr = _side_win_rate(stats, "short")

    p = experience_path_fn(symbol)
    short_loss_spy: list[float] = []
    short_win_spy: list[float] = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[-160:]:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("event") != "exit" or rec.get("side") != "short":
                continue
            spy = rec.get("entry_spy_r5m")
            pnl = rec.get("pnl_usd")
            if spy is None or pnl is None:
                continue
            (short_loss_spy if float(pnl) < 0 else short_win_spy).append(float(spy))

    if len(short_loss_spy) >= 2:
        loss_up = sum(1 for x in short_loss_spy if x > 0.00015)
        if loss_up / len(short_loss_spy) >= 0.55:
            ssf = max(ssf, 0.00035)
            notes.append(f"short_spy_filter_losses={ssf:.5f}")

    if short_ex >= 4 and short_pnl < -0.45 and (short_wr is None or short_wr < 0.45):
        ssf = max(ssf, 0.00075)
        notes.append(f"short_spy_filter_toxic_shorts={ssf:.5f}")
    elif short_ex >= 4 and short_pnl > 0.20:
        ssf = min(ssf, 0.00015)
        notes.append(f"short_spy_filter_loosen={ssf:.5f}")

    if short_win_spy and len(short_win_spy) >= 3:
        win_up = sum(1 for x in short_win_spy if x > 0.00015)
        if win_up / len(short_win_spy) < 0.35 and ssf < 0.0005:
            ssf = 0.00025
            notes.append(f"short_spy_filter_selective={ssf:.5f}")

    params["short_spy_filter"] = round(clamp_param("short_spy_filter", ssf), 5)


def apply_adaptations(
    symbol: str,
    learned: dict[str, Any],
    *,
    experience_path_fn,
) -> list[str]:
    """Run full per-symbol adaptation cycle; returns human-readable adjustment notes."""
    stats = learned["session_stats"]
    params = learned["params"]
    notes: list[str] = []

    _adapt_symbol_pause(params, stats, notes)
    _adapt_entry_thresholds(params, stats, notes)
    _adapt_side_pauses(params, stats, notes)
    _adapt_pattern_deltas(params, learned, notes)
    _adapt_disable_patterns(params, learned, notes)
    _adapt_to_winning_pattern_share(params, learned, notes)
    _adapt_targets_and_cooldown(params, stats, notes)
    _adapt_short_spy_filter(symbol, params, stats, experience_path_fn, notes)
    _adapt_integrity_recommendations(params, notes)

    return notes


def _adapt_integrity_recommendations(params: dict[str, Any], notes: list[str]) -> None:
    """Apply bounded nudges from cross-symbol integrity scan (recursive SI hook)."""
    try:
        from utils.integrity_diagnostics import skim_adaptive_actions

        actions = skim_adaptive_actions()
    except Exception:
        return
    if not actions:
        return
    cm = float(params.get("cooldown_mult") or 1)
    sb = float(params.get("score_bias") or 0)
    if "cooldown_mult" in actions:
        cm = clamp_param("cooldown_mult", cm + float(actions["cooldown_mult"]))
        notes.append(f"integrity_cooldown_mult={cm:.3f}")
    if "score_bias" in actions:
        sb = clamp_param("score_bias", sb + float(actions["score_bias"]))
        notes.append(f"integrity_score_bias={sb:.3f}")
    params["cooldown_mult"] = round(cm, 4)
    params["score_bias"] = round(sb, 4)


def reset_session_adaptive_state(learned: dict[str, Any]) -> None:
    """New ET session: clear session-scoped pauses so Tuesday can reverse; keep causation history."""
    params = learned.setdefault("params", {})
    params["pause_long"] = False
    params["pause_short"] = False
    params["pause_entries"] = False

    causation = ensure_causation(learned)
    causation["eliminated_keys"] = []
    for row in (causation.get("keys") or {}).values():
        if isinstance(row, dict):
            row["eliminated"] = False
