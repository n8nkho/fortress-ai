"""Historical seed quality tiers — relax thresholds for broader (lower-confidence) coverage."""

from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Tier 1 — strict (original bar)
T1_MIN_OCC = _i("FORTRESS_SEED_T1_MIN_OCC", 30)
T1_MIN_CONF = _f("FORTRESS_SEED_T1_MIN_CONF", 0.55)
T1_MAX_CONF = _f("FORTRESS_SEED_T1_MAX_CONF", 0.80)

# Tier 2 — hypothesis
T2_MIN_OCC = _i("FORTRESS_SEED_T2_MIN_OCC", 20)
T2_MIN_CONF = _f("FORTRESS_SEED_T2_MIN_CONF", 0.50)
T2_MAX_CONF = _f("FORTRESS_SEED_T2_MAX_CONF", 0.68)

# Tier 3 — exploratory
T3_MIN_OCC = _i("FORTRESS_SEED_T3_MIN_OCC", 12)
T3_MIN_CONF = _f("FORTRESS_SEED_T3_MIN_CONF", 0.45)
T3_MAX_CONF = _f("FORTRESS_SEED_T3_MAX_CONF", 0.58)


def _edge_strong(wr: float) -> bool:
    """Clear directional edge (original)."""
    return wr >= 0.55 or wr <= 0.45


def _edge_moderate(wr: float) -> bool:
    """Weaker but non–coin-flip band."""
    return wr >= 0.52 or wr <= 0.48


def _edge_weak(wr: float) -> bool:
    """Slight edge; excludes a coin-flip band (0.49, 0.51)."""
    return wr <= 0.49 or wr >= 0.51


def resolve_seed_tier(*, n: int, win_rate: float, laplace_conf: float) -> str | None:
    """
    Pick the strictest tier that qualifies (returns '1', '2', '3', or None).
    Laplace confidence must meet each tier's floor.
    """
    wr = float(win_rate)
    cf = float(laplace_conf)

    if n >= T1_MIN_OCC and _edge_strong(wr) and cf >= T1_MIN_CONF:
        return "1"
    if n >= T2_MIN_OCC and _edge_moderate(wr) and cf >= T2_MIN_CONF:
        return "2"
    if n >= T3_MIN_OCC and _edge_weak(wr) and cf >= T3_MIN_CONF:
        return "3"
    return None


def tier_conf_bounds(tier: str) -> tuple[float, float]:
    t = str(tier or "1").strip()
    if t == "2":
        return (T2_MIN_CONF, T2_MAX_CONF)
    if t == "3":
        return (T3_MIN_CONF, T3_MAX_CONF)
    return (T1_MIN_CONF, T1_MAX_CONF)


def clamp_conf_for_tier(tier: str, raw: float) -> float:
    lo, hi = tier_conf_bounds(tier)
    return max(lo, min(hi, float(raw)))


def tier_label(tier: str) -> str:
    m = {"1": "strong", "2": "hypothesis", "3": "exploratory"}
    return m.get(str(tier), "strong")


def summarize_tiers(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {"1": 0, "2": 0, "3": 0}
    for r in records:
        if not isinstance(r, dict):
            continue
        t = str(r.get("seed_tier") or "1")
        if t in counts:
            counts[t] += 1
    return counts
