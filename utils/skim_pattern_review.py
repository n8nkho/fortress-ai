"""Swarm-wide skim pattern portfolio review — auto action for skim_winning_pattern_share_low."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PATTERNS = ("rip_fade", "pullback_uptrend", "momentum_long", "momentum_short")


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _learned_dir() -> Path:
    return _data_dir() / "skim_swarm" / "learned"


def _merge_pattern_stats(into: dict[str, dict[str, Any]], src: dict[str, Any]) -> None:
    for pattern, ps in (src or {}).items():
        if pattern not in _PATTERNS or not isinstance(ps, dict):
            continue
        row = into.setdefault(
            pattern,
            {"exits": 0, "wins": 0, "losses": 0, "sum_pnl_usd": 0.0},
        )
        row["exits"] = int(row.get("exits") or 0) + int(ps.get("exits") or 0)
        row["wins"] = int(row.get("wins") or 0) + int(ps.get("wins") or 0)
        row["losses"] = int(row.get("losses") or 0) + int(ps.get("losses") or 0)
        row["sum_pnl_usd"] = round(float(row.get("sum_pnl_usd") or 0) + float(ps.get("sum_pnl_usd") or 0), 4)


def swarm_lifetime_pattern_totals() -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    learned_dir = _learned_dir()
    if not learned_dir.is_dir():
        return totals
    for path in learned_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        lps = doc.get("lifetime_pattern_stats") or {}
        if lps:
            _merge_pattern_stats(totals, lps)
        else:
            _merge_pattern_stats(totals, doc.get("pattern_stats") or {})
    return totals


def swarm_winning_pattern_share(*, min_exits: int = 3) -> float | None:
    from agents.skim_swarm.adaptive_policy import winning_pattern_share

    totals = swarm_lifetime_pattern_totals()
    return winning_pattern_share(totals, min_exits=min_exits, disabled=set())


def backfill_lifetime_pattern_stats_from_causation() -> int:
    """One-time migration: aggregate causation keys into lifetime_pattern_stats."""
    learned_dir = _learned_dir()
    updated = 0
    if not learned_dir.is_dir():
        return 0
    for path in learned_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        causation = (doc.get("causation") or {}).get("keys") or {}
        if not causation:
            continue
        lps = doc.setdefault(
            "lifetime_pattern_stats",
            {p: {"exits": 0, "wins": 0, "losses": 0, "sum_pnl_usd": 0.0} for p in _PATTERNS},
        )
        before = json.dumps(lps, sort_keys=True)
        for key, row in causation.items():
            pattern = str(key).split("|", 1)[0]
            if pattern not in _PATTERNS:
                continue
            ps = lps.setdefault(
                pattern,
                {"exits": 0, "wins": 0, "losses": 0, "sum_pnl_usd": 0.0},
            )
            ps["exits"] = int(ps.get("exits") or 0) + int(row.get("exits") or 0)
            ps["wins"] = int(ps.get("wins") or 0) + int(row.get("wins") or 0)
            ps["losses"] = int(ps.get("losses") or 0) + int(row.get("losses") or 0)
            ps["sum_pnl_usd"] = round(float(ps.get("sum_pnl_usd") or 0) + float(row.get("sum_pnl_usd") or 0), 4)
        after = json.dumps(lps, sort_keys=True)
        if after != before:
            doc["lifetime_pattern_stats"] = lps
            path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            updated += 1
    return updated


def apply_swarm_pattern_review(*, min_lifetime_exits: int = 3) -> dict[str, Any]:
    """
    Re-enable lifetime-winning patterns; trim disables that hurt portfolio share.
    Idempotent — safe to run each RTH SI cycle.
    """
    from utils.skim_swarm_config import pattern_disable_min_exits, target_winning_pattern_share

    learned_dir = _learned_dir()
    changes: list[str] = []
    if not learned_dir.is_dir():
        return {"ok": False, "changes": changes, "skipped": "no_learned_dir"}

    backfilled = backfill_lifetime_pattern_stats_from_causation()
    if backfilled:
        changes.append(f"backfill_lifetime_pattern_stats:{backfilled}_symbols")

    min_ex = max(pattern_disable_min_exits(), min_lifetime_exits)
    goal = target_winning_pattern_share()
    portfolio_share = swarm_winning_pattern_share(min_exits=min_ex)

    for path in sorted(learned_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        symbol = str(doc.get("symbol") or path.stem)
        params = doc.setdefault("params", {})
        disabled = set(params.get("disable_patterns") or [])
        lps = doc.get("lifetime_pattern_stats") or {}
        before = sorted(disabled)

        for pattern in _PATTERNS:
            ps = lps.get(pattern) or {}
            ex = int(ps.get("exits") or 0)
            pnl = float(ps.get("sum_pnl_usd") or 0)
            if pattern in disabled and ex >= min_ex and pnl > 0.10:
                disabled.discard(pattern)
                changes.append(f"{symbol}:re_enable_lifetime_winner:{pattern} pnl={pnl:.2f}")

        # Drop disable on patterns with no lifetime evidence (session-only disables).
        for pattern in list(disabled):
            ps = lps.get(pattern) or {}
            if int(ps.get("exits") or 0) == 0:
                disabled.discard(pattern)
                changes.append(f"{symbol}:clear_unproven_disable:{pattern}")

        if len(disabled) >= 3 and portfolio_share is not None and portfolio_share < goal:
            # Keep at most 2 disabled patterns per symbol when portfolio share is low.
            ranked = sorted(
                [(p, float((lps.get(p) or {}).get("sum_pnl_usd") or 0)) for p in disabled],
                key=lambda x: x[1],
            )
            keep = {p for p, _ in ranked[:2]}
            for pattern in list(disabled):
                if pattern not in keep:
                    disabled.discard(pattern)
                    changes.append(f"{symbol}:trim_over_disable:{pattern}")

        after = sorted(disabled)
        if after != before:
            params["disable_patterns"] = after
            doc["params"] = params
            path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "changes": changes,
        "portfolio_share": portfolio_share,
        "goal": goal,
        "symbols_reviewed": len(list(learned_dir.glob("*.json"))),
    }
