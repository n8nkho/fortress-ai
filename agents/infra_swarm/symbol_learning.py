"""Per-symbol recursive improvement — each ticker owns its strategy params."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.infra_swarm.adaptive_policy import apply_adaptations, reset_session_adaptive_state
from agents.infra_swarm.eod import session_date_et
from agents.infra_swarm.intraday_si import (
    adapt_from_block_streaks,
    adapt_last_exit_micro,
    adapt_session_overlay,
    append_adaptation_log,
    ensure_intraday_state,
    estimate_shadow_pnl,
    merge_overlay_into_params,
    maybe_promote_shadow_variant,
    record_block_event,
    record_shadow_exit,
)
from agents.infra_swarm.symbol_causation import (
    build_entry_context,
    causation_blocks_entry,
    causation_summary,
    ensure_causation,
    record_causation_exit,
)
from utils.infra_swarm_config import (
    continuous_si_enabled,
    high_vol_symbols,
    improve_every_exit,
    improve_interval_exits,
    improve_min_exits,
    layer_for_symbol,
    runtime_overrides,
    side_pause_min_exits,
    stop_target_mult,
    swarm_data_dir,
)

_learned_lock = threading.RLock()

_BOUNDS = {
    "enter_long_delta": (-0.15, 0.15),
    "enter_short_delta": (-0.15, 0.15),
    "target_mult": (0.70, 1.35),
    "cooldown_mult": (0.5, 2.5),
    "score_bias": (-0.15, 0.15),
    "short_spy_filter": (0.0, 0.002),
    "pattern_delta": (-0.10, 0.10),
}

_PATTERNS = (
    "layer_catch_up_long",
    "layer_catch_up_short",
    "layer_rip_fade",
    "equipment_capex_confirm",
    "power_parity",
    "stack_momentum_long",
)

_DEFAULT_PARAMS = {
    "enter_long_delta": 0.0,
    "enter_short_delta": 0.0,
    "target_mult": 1.0,
    "cooldown_mult": 1.0,
    "score_bias": 0.0,
    "short_spy_filter": 0.0,
    "pause_long": False,
    "pause_short": False,
    "pause_entries": False,
    "pattern_deltas": {p: 0.0 for p in _PATTERNS},
}

_DEFAULT_SESSION_STATS = {
    "decisions": 0,
    "entries": 0,
    "exits": 0,
    "wins": 0,
    "losses": 0,
    "sum_pnl_usd": 0.0,
    "improvement_cycles": 0,
    "long_pnl_usd": 0.0,
    "short_pnl_usd": 0.0,
    "long_exits": 0,
    "short_exits": 0,
    "long_wins": 0,
    "long_losses": 0,
    "short_wins": 0,
    "short_losses": 0,
}

_DEFAULT_LEARNED = {
    "version": 5,
    "session_date_et": None,
    "params": {
        **_DEFAULT_PARAMS,
        "pattern_deltas": dict(_DEFAULT_PARAMS["pattern_deltas"]),
    },
    "session_stats": dict(_DEFAULT_SESSION_STATS),
    "lifetime_stats": dict(_DEFAULT_SESSION_STATS),
    "pattern_stats": {p: {"exits": 0, "wins": 0, "losses": 0, "sum_pnl_usd": 0.0} for p in _PATTERNS},
    "session_overlay": {
        "enter_long_delta_boost": 0.0,
        "enter_short_delta_boost": 0.0,
        "target_mult_overlay": 1.0,
        "stop_mult_overlay": 1.0,
        "spread_bps_mult": 1.0,
    },
    "block_streaks": {},
    "recent_exit_streak": {"stop_loss": 0, "target_hit": 0, "last_pattern": None},
    "shadow": {
        "variant": "tighter_stop",
        "target_mult_delta": -0.08,
        "live_pnl_usd": 0.0,
        "shadow_pnl_usd": 0.0,
        "live_exits": 0,
        "shadow_exits": 0,
    },
    "adaptation_log": [],
    "causation": {
        "lifetime_exits": 0,
        "keys": {},
        "eliminated_keys": [],
        "top_winners": [],
        "top_losers": [],
    },
    "last_entry_pattern": None,
    "last_entry_side": None,
    "last_entry_spy_r5m": None,
    "last_entry_context": None,
    "last_improvement_utc": None,
    "notes": [],
}


def _learned_dir() -> Path:
    d = swarm_data_dir() / "learned"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _experience_dir() -> Path:
    d = swarm_data_dir() / "experience"
    d.mkdir(parents=True, exist_ok=True)
    return d


def learned_path(symbol: str) -> Path:
    sym = symbol.upper().replace(".", "_")
    return _learned_dir() / f"{sym}.json"


def experience_path(symbol: str) -> Path:
    sym = symbol.upper().replace(".", "_")
    return _experience_dir() / f"{sym}.jsonl"


def _empty_pattern_stats() -> dict[str, dict[str, Any]]:
    return {p: {"exits": 0, "wins": 0, "losses": 0, "sum_pnl_usd": 0.0} for p in _PATTERNS}


def _migrate_v5(data: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(_DEFAULT_LEARNED))
    for key in ("session_date_et", "params", "session_stats", "lifetime_stats", "pattern_stats", "causation"):
        if key in data:
            out[key] = data[key]
    for key in (
        "last_entry_pattern",
        "last_entry_side",
        "last_entry_spy_r5m",
        "last_entry_context",
        "last_improvement_utc",
        "notes",
        "historical_verify",
        "historical_seed_disables",
        "company_beta",
        "symbol",
    ):
        if key in data:
            out[key] = data[key]
    ensure_intraday_state(out)
    return out


def _migrate_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade legacy learned files to v4 layout."""
    out = {
        "version": 4,
        "session_date_et": data.get("session_date_et") or session_date_et(),
        "params": dict(_DEFAULT_PARAMS),
        "session_stats": dict(_DEFAULT_SESSION_STATS),
        "pattern_stats": _empty_pattern_stats(),
        "causation": dict(_DEFAULT_LEARNED["causation"]),
        "last_entry_pattern": None,
        "last_entry_side": None,
        "last_entry_context": None,
        "last_improvement_utc": data.get("last_improvement_utc"),
        "notes": list(data.get("notes") or [])[-8:],
    }
    out["params"]["pattern_deltas"] = dict(_DEFAULT_PARAMS["pattern_deltas"])
    out["params"]["enter_long_delta"] = float(data.get("enter_long_delta") or 0)
    out["params"]["enter_short_delta"] = float(data.get("enter_short_delta") or 0)
    out["params"]["target_mult"] = float(data.get("target_mult") or 1.0)
    out["params"]["cooldown_mult"] = float(data.get("cooldown_mult") or 1.0)
    out["params"]["score_bias"] = float(data.get("score_bias") or 0.0)
    legacy_stats = data.get("stats") or {}
    for k in _DEFAULT_SESSION_STATS:
        if k in legacy_stats:
            out["session_stats"][k] = legacy_stats[k]
    return out


def _fresh_learned(symbol: str) -> dict[str, Any]:
    out = json.loads(json.dumps(_DEFAULT_LEARNED))
    out["symbol"] = symbol.upper()
    out["session_date_et"] = session_date_et()
    out["params"]["pattern_deltas"] = dict(_DEFAULT_PARAMS["pattern_deltas"])
    return out


def _merge_session_into_lifetime(learned: dict[str, Any]) -> None:
    """Roll prior session counters into lifetime_stats before daily reset."""
    ss = learned.get("session_stats") or {}
    if int(ss.get("exits") or 0) <= 0:
        return
    lt = learned.setdefault("lifetime_stats", dict(_DEFAULT_SESSION_STATS))
    for key, val in ss.items():
        if key.endswith("_pnl_usd") or key == "sum_pnl_usd":
            lt[key] = round(float(lt.get(key) or 0) + float(val or 0), 6)
        elif key == "improvement_cycles":
            continue
        else:
            lt[key] = int(lt.get(key) or 0) + int(val or 0)


def _reset_session(learned: dict[str, Any]) -> dict[str, Any]:
    """New ET session: reset daily counters; keep strategy params + lifetime + causation blocks."""
    _merge_session_into_lifetime(learned)
    learned["session_date_et"] = session_date_et()
    learned["session_stats"] = dict(_DEFAULT_SESSION_STATS)
    learned["pattern_stats"] = _empty_pattern_stats()
    learned["last_entry_pattern"] = None
    learned["last_entry_side"] = None
    learned["last_entry_context"] = None
    ensure_causation(learned)
    reset_session_adaptive_state(learned)
    return learned


def load_learned(symbol: str) -> dict[str, Any]:
    p = learned_path(symbol)
    session = session_date_et()
    if not p.exists():
        return _fresh_learned(symbol)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return _fresh_learned(symbol)

    if int(data.get("version") or 0) < 3:
        data = _migrate_v2(data)
    elif int(data.get("version") or 0) < 4:
        data["version"] = 4
        ensure_causation(data)
    if int(data.get("version") or 0) < 5:
        data = _migrate_v5(data)

    if data.get("session_date_et") != session:
        data = _reset_session(data)

    data.setdefault("params", dict(_DEFAULT_PARAMS))
    data["params"].setdefault("pattern_deltas", dict(_DEFAULT_PARAMS["pattern_deltas"]))
    for p in _PATTERNS:
        data["params"]["pattern_deltas"].setdefault(p, 0.0)
    data.setdefault("session_stats", dict(_DEFAULT_SESSION_STATS))
    data.setdefault("pattern_stats", _empty_pattern_stats())
    ensure_causation(data)
    ensure_intraday_state(data)
    data["symbol"] = symbol.upper()
    return data


def save_learned(symbol: str, data: dict[str, Any]) -> None:
    data["symbol"] = symbol.upper()
    data["session_date_et"] = session_date_et()
    ensure_intraday_state(data)
    data["version"] = 5
    data["updated_utc"] = datetime.now(timezone.utc).isoformat()
    learned_path(symbol).write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_experience(symbol: str, record: dict[str, Any]) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with open(experience_path(symbol), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _entry_pattern_from_reasoning(reasoning: str | None) -> str | None:
    if not reasoning:
        return None
    head = str(reasoning).split()[0]
    if head in _PATTERNS:
        return head
    return None


def improve_from_history(symbol: str, *, force: bool = False) -> dict[str, Any] | None:
    """Tune this symbol's params from its own session exits and pattern stats."""
    min_ex = improve_min_exits()
    interval = improve_interval_exits()
    with _learned_lock:
        learned = load_learned(symbol)
        stats = learned["session_stats"]
        exits = int(stats.get("exits") or 0)
        if continuous_si_enabled() and improve_every_exit():
            if exits < 1:
                return None
        elif exits < min_ex:
            return None
        elif not force and (exits - min_ex) % interval != 0:
            return None

        wins = int(stats.get("wins") or 0)
        losses = int(stats.get("losses") or 0)
        closed = max(wins + losses, 1)
        win_rate = wins / closed

        notes = apply_adaptations(symbol, learned, experience_path_fn=experience_path)
        params = learned["params"]
        stats["improvement_cycles"] = int(stats.get("improvement_cycles") or 0) + 1
        learned["last_improvement_utc"] = datetime.now(timezone.utc).isoformat()
        learned["notes"] = (learned.get("notes") or [])[-10:] + notes
        save_learned(symbol, learned)
        return {"symbol": symbol, "win_rate": win_rate, "adjustments": notes, "params": params}


def catch_up_improvement(symbol: str) -> dict[str, Any] | None:
    """Run one improvement when reconcile restored stats but tuning never fired."""
    with _learned_lock:
        learned = load_learned(symbol)
        stats = learned["session_stats"]
        exits = int(stats.get("exits") or 0)
        cycles = int(stats.get("improvement_cycles") or 0)
    if cycles > 0 or exits < improve_min_exits():
        return None
    return improve_from_history(symbol, force=True)


def sync_adaptive_state_on_boot(symbols: list[str] | None = None) -> dict[str, Any]:
    """After reconcile: refresh per-symbol adaptive params; migrate off manual denylist."""
    from utils.infra_swarm_config import runtime_denylist, side_pause_min_exits, swarm_data_dir, universe

    syms = symbols or universe()
    refreshed: list[str] = []
    seeded: list[str] = []
    improved: list[str] = []
    for sym in syms:
        try:
            with _learned_lock:
                learned = load_learned(sym)
                if refresh_historical_seeds(learned):
                    learned["notes"] = (learned.get("notes") or [])[-10:] + ["boot_historical_seed_refresh"]
                    save_learned(sym, learned)
                    seeded.append(sym)
                exits = int(learned["session_stats"].get("exits") or 0)
                if exits >= side_pause_min_exits():
                    notes = apply_adaptations(sym, learned, experience_path_fn=experience_path)
                    if notes:
                        learned["notes"] = (learned.get("notes") or [])[-10:] + ["boot_adaptive_refresh"]
                        save_learned(sym, learned)
                        refreshed.append(sym)
            imp = catch_up_improvement(sym)
            if imp:
                improved.append(sym)
        except Exception:
            continue

    ov_path = swarm_data_dir() / "runtime_overrides.json"
    deny = runtime_denylist()
    cleared: list[str] = []
    if deny and ov_path.exists():
        try:
            ov = json.loads(ov_path.read_text(encoding="utf-8"))
            if ov.pop("denylist_symbols", None):
                ov["denylist_symbols_migrated_utc"] = datetime.now(timezone.utc).isoformat()
                ov["note"] = "Per-symbol pause_entries/pause_long/pause_short adapt in learned/*.json"
                ov_path.write_text(json.dumps(ov, indent=2), encoding="utf-8")
                cleared = sorted(deny)
        except Exception:
            pass

    return {"symbols_seeded": seeded, "symbols_refreshed": refreshed, "symbols_improved": improved, "denylist_cleared": cleared}


def refresh_adaptive_params(symbol: str) -> list[str] | None:
    """Re-run adaptation without incrementing improvement_cycles (boot / reconcile helper)."""
    with _learned_lock:
        learned = load_learned(symbol)
        exits = int(learned["session_stats"].get("exits") or 0)
        if exits < side_pause_min_exits():
            return None
        notes = apply_adaptations(symbol, learned, experience_path_fn=experience_path)
        if notes:
            save_learned(symbol, learned)
        return notes or None


def record_decision(
    symbol: str,
    *,
    decision: dict[str, Any],
    act_result: dict[str, Any],
    features: dict[str, Any],
    entry_price: float | None = None,
) -> dict[str, Any] | None:
    """Update this symbol's experience and session stats."""
    with _learned_lock:
        learned = load_learned(symbol)
        stats = learned["session_stats"]
        stats["decisions"] = int(stats.get("decisions") or 0) + 1
        action = decision.get("action")
        executed = bool(act_result.get("executed"))
        reasoning = decision.get("reasoning")

        append_experience(
            symbol,
            {
                "event": "decision",
                "action": action,
                "executed": executed,
                "reasoning": reasoning,
                "score": decision.get("score"),
                "target_usd": decision.get("target_usd"),
                "block_reason": act_result.get("block_reason"),
                "r5m": features.get("r5m"),
                "spy_r5m": features.get("spy_r5m"),
                "side": features.get("side"),
            },
        )

        improvement = None
        si_notes: list[str] = []
        if executed and action in ("enter_long", "enter_short"):
            stats["entries"] = int(stats.get("entries") or 0) + 1
            pattern = _entry_pattern_from_reasoning(str(reasoning or ""))
            side = "long" if action == "enter_long" else "short"
            learned["last_entry_pattern"] = pattern
            learned["last_entry_side"] = side
            learned["last_entry_spy_r5m"] = features.get("spy_r5m")
            learned["last_entry_context"] = build_entry_context(
                pattern=pattern,
                side=side,
                features=features,
                score=decision.get("score"),
                target_usd=decision.get("target_usd"),
            )
            append_experience(
                symbol,
                {
                    "event": "entry",
                    "pattern": pattern,
                    "side": side,
                    "spy_r5m": features.get("spy_r5m"),
                    "score": decision.get("score"),
                    "causation_key": (learned.get("last_entry_context") or {}).get("causation_key"),
                },
            )
        if executed and action in ("exit_position", "flatten"):
            stats["exits"] = int(stats.get("exits") or 0) + 1
            pnl = features.get("unrealized_usd")
            side = str(learned.get("last_entry_side") or features.get("side") or "")
            pattern = learned.get("last_entry_pattern")
            causation_update = None
            exit_reasoning = str(reasoning or "")
            if pnl is not None:
                try:
                    pv = float(pnl)
                    stats["sum_pnl_usd"] = round(float(stats.get("sum_pnl_usd") or 0) + pv, 4)
                    if pv >= 0:
                        stats["wins"] = int(stats.get("wins") or 0) + 1
                    else:
                        stats["losses"] = int(stats.get("losses") or 0) + 1
                    if side == "long":
                        stats["long_pnl_usd"] = round(float(stats.get("long_pnl_usd") or 0) + pv, 4)
                        stats["long_exits"] = int(stats.get("long_exits") or 0) + 1
                        if pv >= 0:
                            stats["long_wins"] = int(stats.get("long_wins") or 0) + 1
                        else:
                            stats["long_losses"] = int(stats.get("long_losses") or 0) + 1
                    elif side == "short":
                        stats["short_pnl_usd"] = round(float(stats.get("short_pnl_usd") or 0) + pv, 4)
                        stats["short_exits"] = int(stats.get("short_exits") or 0) + 1
                        if pv >= 0:
                            stats["short_wins"] = int(stats.get("short_wins") or 0) + 1
                        else:
                            stats["short_losses"] = int(stats.get("short_losses") or 0) + 1
                    if pattern and pattern in learned.get("pattern_stats", {}):
                        ps = learned["pattern_stats"][pattern]
                        ps["exits"] = int(ps.get("exits") or 0) + 1
                        ps["sum_pnl_usd"] = round(float(ps.get("sum_pnl_usd") or 0) + pv, 4)
                        if pv >= 0:
                            ps["wins"] = int(ps.get("wins") or 0) + 1
                        else:
                            ps["losses"] = int(ps.get("losses") or 0) + 1
                    causation_update = record_causation_exit(
                        learned,
                        entry_context=learned.get("last_entry_context"),
                        exit_reasoning=exit_reasoning,
                        pnl_usd=pv,
                    )
                    adapt_last_exit_micro(
                        learned,
                        learned["params"],
                        exit_reasoning=exit_reasoning,
                        pnl_usd=pv,
                        pattern=pattern,
                        notes=si_notes,
                    )
                    record_shadow_exit(
                        learned,
                        pnl_usd=pv,
                        shadow_pnl_usd=estimate_shadow_pnl(pv, exit_reasoning),
                    )
                    maybe_promote_shadow_variant(learned, learned["params"], si_notes)
                    adapt_session_overlay(learned, si_notes)
                    append_experience(
                        symbol,
                        {
                            "event": "exit",
                            "pattern": pattern,
                            "side": side,
                            "pnl_usd": pv,
                            "entry_spy_r5m": learned.get("last_entry_spy_r5m"),
                            "causation_key": (learned.get("last_entry_context") or {}).get("causation_key"),
                            "exit_class": (causation_update or {}).get("exit_class"),
                            "eliminated_key": (causation_update or {}).get("eliminated_key"),
                        },
                    )
                except (TypeError, ValueError):
                    pass
            append_adaptation_log(learned, si_notes)
            learned["last_entry_pattern"] = None
            learned["last_entry_side"] = None
            learned["last_entry_spy_r5m"] = None
            learned["last_entry_context"] = None
            save_learned(symbol, learned)
            if continuous_si_enabled() and improve_every_exit():
                improvement = improve_from_history(symbol, force=True)
            else:
                improvement = improve_from_history(symbol)
            if improvement and causation_update:
                improvement["causation"] = causation_update
        else:
            block = act_result.get("block_reason") or (reasoning if action == "wait" else None)
            if block and action == "wait" and not executed:
                record_block_event(learned, str(block))
                adapt_from_block_streaks(learned, learned["params"], si_notes)
                append_adaptation_log(learned, si_notes)
            save_learned(symbol, learned)

        return improvement


def get_causation(symbol: str) -> dict[str, Any]:
    return causation_summary(load_learned(symbol))


def entry_blocked_by_causation(
    symbol: str,
    *,
    pattern: str,
    side: str,
    features: dict[str, Any],
    score: float,
) -> tuple[bool, str | None]:
    learned = load_learned(symbol)
    return causation_blocks_entry(
        symbol,
        learned,
        pattern=pattern,
        side=side,
        features=features,
        score=score,
    )


_HISTORICAL_SEED_KEYS = (
    "enter_long_delta",
    "enter_short_delta",
    "target_mult",
    "short_spy_filter",
    "score_bias",
    "disable_patterns",
)


def _historical_recommended(learned: dict[str, Any]) -> dict[str, Any]:
    hv = learned.get("historical_verify") or {}
    return hv.get("recommended_params") or {}


def refresh_historical_seeds(learned: dict[str, Any]) -> bool:
    """Reset seed params from historical_verify unless live lifetime record is toxic."""
    lt = learned.get("lifetime_stats") or {}
    lt_exits = int(lt.get("exits") or 0)
    lt_pnl = float(lt.get("sum_pnl_usd") or 0)
    if lt_exits >= 10 and lt_pnl < 0:
        return False
    rec = _historical_recommended(learned)
    if not rec:
        return False
    params = learned.setdefault("params", {})
    changed = False
    for k in _HISTORICAL_SEED_KEYS:
        if k not in rec or rec[k] is None:
            continue
        val = list(rec[k]) if k == "disable_patterns" else rec[k]
        if params.get(k) != val:
            params[k] = val
            changed = True
    seed_disables = list(rec.get("disable_patterns") or [])
    if learned.get("historical_seed_disables") != seed_disables:
        learned["historical_seed_disables"] = seed_disables
        changed = True
    if len(seed_disables) >= len(_PATTERNS):
        if not params.get("pause_entries"):
            params["pause_entries"] = True
            changed = True
    elif params.get("pause_entries") and len(seed_disables) < len(_PATTERNS):
        # Historical seed re-enabled at least one pattern — clear full pause from prior all-disabled seed.
        params["pause_entries"] = False
        changed = True
    return changed


def promote_historical_verify_params(learned: dict[str, Any]) -> bool:
    """Legacy helper — prefer refresh_historical_seeds on boot."""
    return refresh_historical_seeds(learned)


def _disable_patterns_from_learned(learned: dict[str, Any]) -> list[str]:
    params = learned.get("params") or {}
    if params.get("disable_patterns") is not None:
        return list(params.get("disable_patterns") or [])
    rec = _historical_recommended(learned)
    return list(rec.get("disable_patterns") or [])


def _review_param_overrides(symbol: str) -> dict[str, Any]:
    ov = runtime_overrides()
    review = ov.get("review_actions") if isinstance(ov.get("review_actions"), dict) else {}
    pause_syms = {str(s).upper() for s in (review.get("pause_symbols") or [])}
    out: dict[str, Any] = {}
    if symbol.upper() in pause_syms:
        out["pause_entries"] = True
    param_ov = (review.get("param_overrides") or {}).get(symbol.upper())
    if isinstance(param_ov, dict):
        out.update(param_ov)
    return out


def get_params(symbol: str) -> dict[str, float | dict[str, float]]:
    L = load_learned(symbol)
    ensure_intraday_state(L)
    P = dict(L.get("params") or {})
    P.update(_review_param_overrides(symbol))
    layer = layer_for_symbol(symbol)
    base_long = 0.20 if layer == "L1" else 0.22 if layer == "L3" else 0.21
    base_short = -base_long
    pd = P.get("pattern_deltas") or {}
    overlay = merge_overlay_into_params(P, L)
    return {
        "enter_long": base_long + float(P.get("enter_long_delta") or 0) + float(overlay.get("enter_long_delta_boost") or 0),
        "enter_short": base_short + float(P.get("enter_short_delta") or 0) + float(overlay.get("enter_short_delta_boost") or 0),
        "target_mult": float(P.get("target_mult") or 1.0),
        "target_mult_effective": float(overlay.get("target_mult_effective") or P.get("target_mult") or 1.0),
        "stop_target_mult_effective": float(overlay.get("stop_target_mult_effective") or stop_target_mult()),
        "spread_bps_mult": float(overlay.get("spread_bps_mult") or 1.0),
        "cooldown_mult": float(P.get("cooldown_mult") or 1.0),
        "score_bias": float(P.get("score_bias") or 0.0),
        "short_spy_filter": float(P.get("short_spy_filter") or 0.0),
        "pause_long": bool(P.get("pause_long")),
        "pause_short": bool(P.get("pause_short")),
        "pause_entries": bool(P.get("pause_entries")),
        "pattern_deltas": {p: float(pd.get(p) or 0.0) for p in _PATTERNS},
        "disable_patterns": _disable_patterns_from_learned(L),
        "layer": layer,
    }


def default_cooldown_sec(symbol: str) -> float:
    sym = symbol.upper()
    learned = load_learned(sym)
    beta = None
    base = 90.0
    ctx_beta = learned.get("company_beta")
    if ctx_beta is not None:
        beta = float(ctx_beta)
    params = learned.get("params") or {}
    tm = float(params.get("target_mult") or 1.0)
    if tm > 1.15:
        base = 120.0
    elif sym in high_vol_symbols():
        base = 105.0
    ov = runtime_overrides()
    base *= float(ov.get("cooldown_mult_boost") or 1.0)
    sec = base * float(params.get("cooldown_mult") or 1.0)
    min_sec = float(ov.get("min_cooldown_sec") or 0)
    if min_sec > 0:
        sec = max(sec, min_sec)
    if beta is not None and beta > 1.4:
        sec *= 1.15
    return sec
