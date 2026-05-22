"""Per-symbol recursive improvement — each ticker owns its strategy params."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.skim_swarm.eod import session_date_et
from agents.skim_swarm.symbol_causation import (
    build_entry_context,
    causation_blocks_entry,
    causation_summary,
    ensure_causation,
    record_causation_exit,
)
from utils.skim_swarm_config import improve_interval_exits, improve_min_exits, runtime_overrides, swarm_data_dir, thin_etf_symbols

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

_PATTERNS = ("rip_fade", "pullback_uptrend", "momentum_long", "momentum_short")

_DEFAULT_PARAMS = {
    "enter_long_delta": 0.0,
    "enter_short_delta": 0.0,
    "target_mult": 1.0,
    "cooldown_mult": 1.0,
    "score_bias": 0.0,
    "short_spy_filter": 0.0,
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
}

_DEFAULT_LEARNED = {
    "version": 4,
    "session_date_et": None,
    "params": {
        **_DEFAULT_PARAMS,
        "pattern_deltas": dict(_DEFAULT_PARAMS["pattern_deltas"]),
    },
    "session_stats": dict(_DEFAULT_SESSION_STATS),
    "pattern_stats": {p: {"exits": 0, "wins": 0, "losses": 0, "sum_pnl_usd": 0.0} for p in _PATTERNS},
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


def _reset_session(learned: dict[str, Any]) -> dict[str, Any]:
    """New ET session: reset daily counters; keep strategy params + causation history."""
    learned["session_date_et"] = session_date_et()
    learned["session_stats"] = dict(_DEFAULT_SESSION_STATS)
    learned["pattern_stats"] = _empty_pattern_stats()
    learned["last_entry_pattern"] = None
    learned["last_entry_side"] = None
    learned["last_entry_context"] = None
    ensure_causation(learned)
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

    if data.get("session_date_et") != session:
        data = _reset_session(data)

    data.setdefault("params", dict(_DEFAULT_PARAMS))
    data["params"].setdefault("pattern_deltas", dict(_DEFAULT_PARAMS["pattern_deltas"]))
    for p in _PATTERNS:
        data["params"]["pattern_deltas"].setdefault(p, 0.0)
    data.setdefault("session_stats", dict(_DEFAULT_SESSION_STATS))
    data.setdefault("pattern_stats", _empty_pattern_stats())
    ensure_causation(data)
    data["symbol"] = symbol.upper()
    return data


def save_learned(symbol: str, data: dict[str, Any]) -> None:
    data["symbol"] = symbol.upper()
    data["session_date_et"] = session_date_et()
    data["version"] = 4
    data["updated_utc"] = datetime.now(timezone.utc).isoformat()
    learned_path(symbol).write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_experience(symbol: str, record: dict[str, Any]) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with open(experience_path(symbol), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _clamp_param(name: str, val: float) -> float:
    lo, hi = _BOUNDS.get(name, (-1.0, 1.0))
    return max(lo, min(hi, val))


def _entry_pattern_from_reasoning(reasoning: str | None) -> str | None:
    if not reasoning:
        return None
    head = str(reasoning).split()[0]
    if head in _PATTERNS:
        return head
    return None


def _analyze_short_spy_experience(symbol: str) -> float | None:
    """Learn per-symbol SPY filter for shorts from entry experience."""
    p = experience_path(symbol)
    if not p.exists():
        return None
    short_loss_spy: list[float] = []
    short_win_spy: list[float] = []
    for line in p.read_text(encoding="utf-8").splitlines()[-120:]:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "exit":
            continue
        if rec.get("side") != "short":
            continue
        spy = rec.get("entry_spy_r5m")
        pnl = rec.get("pnl_usd")
        if spy is None or pnl is None:
            continue
        (short_loss_spy if float(pnl) < 0 else short_win_spy).append(float(spy))
    if len(short_loss_spy) < 3:
        return None
    loss_up = sum(1 for x in short_loss_spy if x > 0.0002)
    if loss_up / len(short_loss_spy) >= 0.6:
        return 0.00025
    if short_win_spy and sum(1 for x in short_win_spy if x > 0.0002) / len(short_win_spy) >= 0.5:
        return 0.0
    return None


def improve_from_history(symbol: str) -> dict[str, Any] | None:
    """Tune this symbol's params from its own session exits and pattern stats."""
    min_ex = improve_min_exits()
    interval = improve_interval_exits()
    with _learned_lock:
        learned = load_learned(symbol)
        stats = learned["session_stats"]
        exits = int(stats.get("exits") or 0)
        if exits < min_ex:
            return None
        if (exits - min_ex) % interval != 0:
            return None

        wins = int(stats.get("wins") or 0)
        losses = int(stats.get("losses") or 0)
        closed = max(wins + losses, 1)
        win_rate = wins / closed
        sum_pnl = float(stats.get("sum_pnl_usd") or 0)

        params = learned["params"]
        notes: list[str] = []
        el = float(params.get("enter_long_delta") or 0)
        es = float(params.get("enter_short_delta") or 0)
        tm = float(params.get("target_mult") or 1)
        cm = float(params.get("cooldown_mult") or 1)
        sb = float(params.get("score_bias") or 0)
        ssf = float(params.get("short_spy_filter") or 0)
        pd = dict(params.get("pattern_deltas") or {})

        long_pnl = float(stats.get("long_pnl_usd") or 0)
        short_pnl = float(stats.get("short_pnl_usd") or 0)
        long_ex = int(stats.get("long_exits") or 0)
        short_ex = int(stats.get("short_exits") or 0)

        if long_ex >= 3 and long_pnl < -0.75:
            el += 0.02
            notes.append(f"tighten_long pnl={long_pnl:.2f}")
        elif long_ex >= 5 and long_pnl > 0.5:
            el -= 0.01
            notes.append(f"loosen_long pnl={long_pnl:.2f}")

        if short_ex >= 3 and short_pnl < -0.75:
            es += 0.02
            notes.append(f"tighten_short pnl={short_pnl:.2f}")
        elif short_ex >= 5 and short_pnl > 0.5:
            es -= 0.01
            notes.append(f"loosen_short pnl={short_pnl:.2f}")

        for pattern, ps in (learned.get("pattern_stats") or {}).items():
            p_ex = int(ps.get("exits") or 0)
            p_pnl = float(ps.get("sum_pnl_usd") or 0)
            if p_ex < 3:
                continue
            p_wr = int(ps.get("wins") or 0) / max(p_ex, 1)
            cur = float(pd.get(pattern) or 0)
            if p_pnl < -0.5 or p_wr < 0.35:
                pd[pattern] = round(_clamp_param("pattern_delta", cur + 0.025), 4)
                notes.append(f"tighten_{pattern} pnl={p_pnl:.2f}")
            elif p_pnl > 0.4 and p_wr > 0.55:
                pd[pattern] = round(_clamp_param("pattern_delta", cur - 0.015), 4)
                notes.append(f"loosen_{pattern} pnl={p_pnl:.2f}")

        causation = ensure_causation(learned)
        for key in causation.get("eliminated_keys") or []:
            parts = str(key).split("|")
            if not parts:
                continue
            pat = parts[0]
            if pat in pd:
                pd[pat] = round(_clamp_param("pattern_delta", float(pd.get(pat) or 0) + 0.03), 4)
                notes.append(f"causation_eliminated:{key[:48]}")

        if win_rate < 0.38:
            tm *= 0.94
            cm *= 1.10
            notes.append(f"shrink_targets win_rate={win_rate:.2f}")
        elif win_rate > 0.62 and sum_pnl > 0.75 and exits >= 6:
            tm *= 0.98
            notes.append(f"loosen_targets win_rate={win_rate:.2f}")

        if losses >= 3 and sum_pnl < 0:
            cm *= 1.12
            sb *= 0.5
            notes.append("loss_streak_cooldown")

        learned_spy = _analyze_short_spy_experience(symbol)
        if learned_spy is not None:
            ssf = learned_spy
            notes.append(f"short_spy_filter={ssf:.5f}")

        params["enter_long_delta"] = round(_clamp_param("enter_long_delta", el), 4)
        params["enter_short_delta"] = round(_clamp_param("enter_short_delta", es), 4)
        params["target_mult"] = round(_clamp_param("target_mult", tm), 4)
        params["cooldown_mult"] = round(_clamp_param("cooldown_mult", cm), 4)
        params["score_bias"] = round(_clamp_param("score_bias", sb), 4)
        params["short_spy_filter"] = round(_clamp_param("short_spy_filter", ssf), 5)
        params["pattern_deltas"] = pd
        stats["improvement_cycles"] = int(stats.get("improvement_cycles") or 0) + 1
        learned["last_improvement_utc"] = datetime.now(timezone.utc).isoformat()
        learned["notes"] = (learned.get("notes") or [])[-8:] + notes
        save_learned(symbol, learned)
        return {"symbol": symbol, "win_rate": win_rate, "adjustments": notes, "params": params}


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
                    elif side == "short":
                        stats["short_pnl_usd"] = round(float(stats.get("short_pnl_usd") or 0) + pv, 4)
                        stats["short_exits"] = int(stats.get("short_exits") or 0) + 1
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
                        exit_reasoning=str(reasoning or ""),
                        pnl_usd=pv,
                    )
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
            learned["last_entry_pattern"] = None
            learned["last_entry_side"] = None
            learned["last_entry_spy_r5m"] = None
            learned["last_entry_context"] = None
            save_learned(symbol, learned)
            improvement = improve_from_history(symbol)
            if improvement and causation_update:
                improvement["causation"] = causation_update
        else:
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


def get_params(symbol: str) -> dict[str, float | dict[str, float]]:
    L = load_learned(symbol)
    P = L.get("params") or {}
    thin = symbol in thin_etf_symbols()
    base_long = 0.24 if thin else 0.22
    base_short = -0.24 if thin else -0.22
    pd = P.get("pattern_deltas") or {}
    return {
        "enter_long": base_long + float(P.get("enter_long_delta") or 0),
        "enter_short": base_short + float(P.get("enter_short_delta") or 0),
        "target_mult": float(P.get("target_mult") or 1.0),
        "cooldown_mult": float(P.get("cooldown_mult") or 1.0),
        "score_bias": float(P.get("score_bias") or 0.0),
        "short_spy_filter": float(P.get("short_spy_filter") or 0.0),
        "pattern_deltas": {p: float(pd.get(p) or 0.0) for p in _PATTERNS},
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
    elif sym in thin_etf_symbols():
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
