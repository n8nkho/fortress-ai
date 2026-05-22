"""Per-symbol recursive improvement from trades and decisions (no LLM)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.skim_swarm_config import swarm_data_dir, thin_etf_symbols

_BOUNDS = {
    "enter_long_delta": (-0.12, 0.12),
    "enter_short_delta": (-0.12, 0.12),
    "target_mult": (0.75, 1.45),
    "cooldown_mult": (0.6, 2.0),
    "score_bias": (-0.2, 0.2),
}

_DEFAULT_LEARNED = {
    "version": 1,
    "enter_long_delta": 0.0,
    "enter_short_delta": 0.0,
    "target_mult": 1.0,
    "cooldown_mult": 1.0,
    "score_bias": 0.0,
    "stats": {
        "decisions": 0,
        "entries": 0,
        "exits": 0,
        "wins": 0,
        "losses": 0,
        "sum_pnl_usd": 0.0,
        "improvement_cycles": 0,
    },
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


def load_learned(symbol: str) -> dict[str, Any]:
    p = learned_path(symbol)
    if not p.exists():
        out = dict(_DEFAULT_LEARNED)
        out["symbol"] = symbol.upper()
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        for k, v in _DEFAULT_LEARNED.items():
            data.setdefault(k, v if not isinstance(v, dict) else dict(v))
        data.setdefault("stats", {})
        for sk, sv in _DEFAULT_LEARNED["stats"].items():
            data["stats"].setdefault(sk, sv)
        return data
    except Exception:
        return dict(_DEFAULT_LEARNED)


def save_learned(symbol: str, data: dict[str, Any]) -> None:
    data["symbol"] = symbol.upper()
    data["updated_utc"] = datetime.now(timezone.utc).isoformat()
    learned_path(symbol).write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_experience(symbol: str, record: dict[str, Any]) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with open(experience_path(symbol), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _clamp_param(name: str, val: float) -> float:
    lo, hi = _BOUNDS.get(name, (-1.0, 1.0))
    return max(lo, min(hi, val))


def improve_from_history(symbol: str) -> dict[str, Any] | None:
    """
    Tune thresholds from recent exits. Runs after enough closed trades.
    """
    learned = load_learned(symbol)
    stats = learned["stats"]
    exits = int(stats.get("exits") or 0)
    if exits < 3 or exits % 3 != 0:
        return None

    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    closed = max(wins + losses, 1)
    win_rate = wins / closed
    sum_pnl = float(stats.get("sum_pnl_usd") or 0)

    notes: list[str] = []
    el = float(learned.get("enter_long_delta") or 0)
    es = float(learned.get("enter_short_delta") or 0)
    tm = float(learned.get("target_mult") or 1)
    cm = float(learned.get("cooldown_mult") or 1)
    sb = float(learned.get("score_bias") or 0)

    if win_rate < 0.4:
        el += 0.02
        es -= 0.02
        tm *= 1.05
        cm *= 1.1
        notes.append(f"tighten_entries win_rate={win_rate:.2f}")
    elif win_rate > 0.58 and sum_pnl > 0:
        el -= 0.015
        es += 0.015
        tm *= 0.97
        notes.append(f"loosen_entries win_rate={win_rate:.2f}")
    if losses >= 3 and sum_pnl < 0:
        sb -= 0.02
        tm *= 1.08
        notes.append("loss_streak_defensive")

    learned["enter_long_delta"] = round(_clamp_param("enter_long_delta", el), 4)
    learned["enter_short_delta"] = round(_clamp_param("enter_short_delta", es), 4)
    learned["target_mult"] = round(_clamp_param("target_mult", tm), 4)
    learned["cooldown_mult"] = round(_clamp_param("cooldown_mult", cm), 4)
    learned["score_bias"] = round(_clamp_param("score_bias", sb), 4)
    stats["improvement_cycles"] = int(stats.get("improvement_cycles") or 0) + 1
    learned["last_improvement_utc"] = datetime.now(timezone.utc).isoformat()
    learned["notes"] = (learned.get("notes") or [])[-8:] + notes
    save_learned(symbol, learned)
    return {"symbol": symbol, "win_rate": win_rate, "adjustments": notes, "learned": learned}


def record_decision(
    symbol: str,
    *,
    decision: dict[str, Any],
    act_result: dict[str, Any],
    features: dict[str, Any],
    entry_price: float | None = None,
) -> dict[str, Any] | None:
    """Update stats and experience; return improvement summary if tuned."""
    learned = load_learned(symbol)
    stats = learned["stats"]
    stats["decisions"] = int(stats.get("decisions") or 0) + 1
    action = decision.get("action")
    executed = bool(act_result.get("executed"))

    append_experience(
        symbol,
        {
            "action": action,
            "executed": executed,
            "reasoning": decision.get("reasoning"),
            "score": decision.get("score"),
            "target_usd": decision.get("target_usd"),
            "block_reason": act_result.get("block_reason"),
            "r5m": features.get("r5m"),
            "side": features.get("side"),
        },
    )

    improvement = None
    if executed and action in ("enter_long", "enter_short"):
        stats["entries"] = int(stats.get("entries") or 0) + 1
    if executed and action in ("exit_position", "flatten"):
        stats["exits"] = int(stats.get("exits") or 0) + 1
        pnl = features.get("unrealized_usd")
        if pnl is not None:
            try:
                pv = float(pnl)
                stats["sum_pnl_usd"] = round(float(stats.get("sum_pnl_usd") or 0) + pv, 4)
                if pv >= 0:
                    stats["wins"] = int(stats.get("wins") or 0) + 1
                else:
                    stats["losses"] = int(stats.get("losses") or 0) + 1
            except (TypeError, ValueError):
                pass
        save_learned(symbol, learned)
        improvement = improve_from_history(symbol)
    else:
        save_learned(symbol, learned)

    return improvement


def get_params(symbol: str) -> dict[str, float]:
    L = load_learned(symbol)
    thin = symbol in thin_etf_symbols()
    base_long = 0.22 if thin else 0.18
    base_short = -0.22 if thin else -0.18
    return {
        "enter_long": base_long + float(L.get("enter_long_delta") or 0),
        "enter_short": base_short + float(L.get("enter_short_delta") or 0),
        "target_mult": float(L.get("target_mult") or 1.0),
        "cooldown_mult": float(L.get("cooldown_mult") or 1.0),
        "score_bias": float(L.get("score_bias") or 0.0),
    }


def default_cooldown_sec(symbol: str) -> float:
    base = 45.0 if symbol in thin_etf_symbols() else 30.0
    return base * get_params(symbol)["cooldown_mult"]
