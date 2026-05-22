"""Per-symbol causation: accumulate what entry contexts cause wins vs losses."""
from __future__ import annotations

from typing import Any

# Minimum closed trades before a causal key can block entries for this symbol only.
_MIN_SAMPLES_BLOCK = 4
_MIN_SAMPLES_STRONG_BLOCK = 3

# Loss thresholds (per-symbol cumulative on one causal key).
_BLOCK_PNL_SOFT = -0.40
_BLOCK_PNL_HARD = -1.00
_BLOCK_WIN_RATE = 0.35


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _bucket_signed(v: float, *, flat: float = 0.00015) -> str:
    if v > flat:
        return "pos"
    if v < -flat:
        return "neg"
    return "flat"


def _score_bucket(score: float) -> str:
    a = abs(score)
    if a >= 0.35:
        return "strong"
    if a >= 0.18:
        return "med"
    return "weak"


def _exit_reason_class(reasoning: str | None) -> str:
    if not reasoning:
        return "unknown"
    head = str(reasoning).split(":")[0]
    if head.startswith("stop_loss"):
        return "stop_loss"
    if head.startswith("skim_target_hit"):
        return "target_hit"
    if head == "trailing_giveback":
        return "trailing_giveback"
    if head == "flatten" or "eod" in head:
        return "flatten"
    return head


def build_causation_key(
    *,
    pattern: str | None,
    side: str | None,
    features: dict[str, Any],
    score: float | None = None,
) -> str | None:
    """Discrete context key unique to this symbol's experience buckets."""
    if not pattern or not side:
        return None
    sc = _f(score if score is not None else features.get("score"))
    spy = _bucket_signed(_f(features.get("spy_r5m")))
    sym_trend = _bucket_signed(_f(features.get("r5m")))
    return "|".join(
        [
            pattern,
            side,
            f"spy_{spy}",
            f"sym_{sym_trend}",
            f"score_{_score_bucket(sc)}",
        ]
    )


def _empty_causation() -> dict[str, Any]:
    return {
        "lifetime_exits": 0,
        "keys": {},
        "eliminated_keys": [],
        "top_winners": [],
        "top_losers": [],
    }


def ensure_causation(learned: dict[str, Any]) -> dict[str, Any]:
    c = learned.get("causation")
    if not isinstance(c, dict):
        c = _empty_causation()
    c.setdefault("lifetime_exits", 0)
    c.setdefault("keys", {})
    c.setdefault("eliminated_keys", [])
    c.setdefault("top_winners", [])
    c.setdefault("top_losers", [])
    learned["causation"] = c
    return c


def _key_row(keys: dict[str, Any], causation_key: str) -> dict[str, Any]:
    row = keys.get(causation_key)
    if not isinstance(row, dict):
        row = {
            "exits": 0,
            "wins": 0,
            "losses": 0,
            "sum_pnl_usd": 0.0,
            "stop_loss": 0,
            "target_hit": 0,
            "trailing_giveback": 0,
            "other_exit": 0,
            "eliminated": False,
            "last_pnl_usd": 0.0,
        }
        keys[causation_key] = row
    return row


def _should_eliminate(row: dict[str, Any]) -> bool:
    exits = int(row.get("exits") or 0)
    if exits < _MIN_SAMPLES_STRONG_BLOCK:
        return False
    pnl = float(row.get("sum_pnl_usd") or 0)
    wins = int(row.get("wins") or 0)
    wr = wins / max(exits, 1)
    if exits >= _MIN_SAMPLES_BLOCK and pnl <= _BLOCK_PNL_SOFT and wr < _BLOCK_WIN_RATE:
        return True
    if exits >= _MIN_SAMPLES_STRONG_BLOCK and pnl <= _BLOCK_PNL_HARD:
        return True
    return False


def _refresh_rankings(causation: dict[str, Any]) -> None:
    keys = causation.get("keys") or {}
    ranked: list[tuple[float, int, str, dict]] = []
    for k, row in keys.items():
        ex = int(row.get("exits") or 0)
        if ex < 2:
            continue
        ranked.append((float(row.get("sum_pnl_usd") or 0), ex, k, row))
    ranked.sort(key=lambda x: x[0])
    causation["top_losers"] = [
        {
            "key": k,
            "exits": ex,
            "sum_pnl_usd": round(pnl, 4),
            "win_rate": round(int(row.get("wins") or 0) / max(ex, 1), 3),
            "stop_loss": int(row.get("stop_loss") or 0),
            "eliminated": bool(row.get("eliminated")),
        }
        for pnl, ex, k, row in ranked[:5]
    ]
    causation["top_winners"] = [
        {
            "key": k,
            "exits": ex,
            "sum_pnl_usd": round(pnl, 4),
            "win_rate": round(int(row.get("wins") or 0) / max(ex, 1), 3),
            "target_hit": int(row.get("target_hit") or 0),
            "eliminated": bool(row.get("eliminated")),
        }
        for pnl, ex, k, row in sorted(ranked, key=lambda x: x[0], reverse=True)[:5]
    ]


def record_causation_exit(
    learned: dict[str, Any],
    *,
    entry_context: dict[str, Any] | None,
    exit_reasoning: str | None,
    pnl_usd: float,
) -> dict[str, Any] | None:
    """Accumulate exit outcome against the entry causation key for this symbol."""
    if not entry_context:
        return None
    key = entry_context.get("causation_key")
    if not key:
        key = build_causation_key(
            pattern=entry_context.get("pattern"),
            side=entry_context.get("side"),
            features=entry_context,
            score=entry_context.get("score"),
        )
    if not key:
        return None

    causation = ensure_causation(learned)
    keys = causation["keys"]
    row = _key_row(keys, key)
    row["exits"] = int(row.get("exits") or 0) + 1
    row["sum_pnl_usd"] = round(float(row.get("sum_pnl_usd") or 0) + float(pnl_usd), 4)
    row["last_pnl_usd"] = round(float(pnl_usd), 4)
    if pnl_usd >= 0:
        row["wins"] = int(row.get("wins") or 0) + 1
    else:
        row["losses"] = int(row.get("losses") or 0) + 1

    ex_cls = _exit_reason_class(exit_reasoning)
    if ex_cls == "stop_loss":
        row["stop_loss"] = int(row.get("stop_loss") or 0) + 1
    elif ex_cls == "target_hit":
        row["target_hit"] = int(row.get("target_hit") or 0) + 1
    elif ex_cls == "trailing_giveback":
        row["trailing_giveback"] = int(row.get("trailing_giveback") or 0) + 1
    else:
        row["other_exit"] = int(row.get("other_exit") or 0) + 1

    eliminated_now = False
    if _should_eliminate(row):
        row["eliminated"] = True
        eliminated = set(causation.get("eliminated_keys") or [])
        if key not in eliminated:
            eliminated.add(key)
            eliminated_now = True
        causation["eliminated_keys"] = sorted(eliminated)

    causation["lifetime_exits"] = int(causation.get("lifetime_exits") or 0) + 1
    _refresh_rankings(causation)

    return {
        "causation_key": key,
        "pnl_usd": round(float(pnl_usd), 4),
        "exit_class": ex_cls,
        "eliminated_key": key if eliminated_now else None,
        "row": dict(row),
    }


def causation_blocks_entry(
    symbol: str,
    learned: dict[str, Any],
    *,
    pattern: str,
    side: str,
    features: dict[str, Any],
    score: float,
) -> tuple[bool, str | None]:
    """Return True if this symbol's history says this entry context causes losses."""
    del symbol
    key = build_causation_key(pattern=pattern, side=side, features=features, score=score)
    if not key:
        return False, None
    causation = ensure_causation(learned)
    if key in set(causation.get("eliminated_keys") or []):
        return True, f"causation_eliminated:{key}"
    row = (causation.get("keys") or {}).get(key) or {}
    if row.get("eliminated"):
        return True, f"causation_eliminated:{key}"
    return False, None


def build_entry_context(
    *,
    pattern: str | None,
    side: str | None,
    features: dict[str, Any],
    score: float | None,
    target_usd: float | None,
) -> dict[str, Any]:
    ctx = {
        "pattern": pattern,
        "side": side,
        "score": score,
        "r1m": features.get("r1m"),
        "r5m": features.get("r5m"),
        "spy_r5m": features.get("spy_r5m"),
        "rsi1m": features.get("rsi1m"),
        "residual_vs_spy": features.get("residual_vs_spy"),
        "spread_bps": features.get("spread_bps"),
        "target_usd": target_usd,
    }
    ctx["causation_key"] = build_causation_key(
        pattern=pattern, side=side, features={**features, "score": score}, score=score
    )
    return ctx


def causation_summary(learned: dict[str, Any]) -> dict[str, Any]:
    """Compact per-symbol causation report for logs / dashboard."""
    c = ensure_causation(learned)
    return {
        "lifetime_exits": int(c.get("lifetime_exits") or 0),
        "tracked_keys": len(c.get("keys") or {}),
        "eliminated_keys": list(c.get("eliminated_keys") or [])[:8],
        "top_winners": c.get("top_winners") or [],
        "top_losers": c.get("top_losers") or [],
    }
