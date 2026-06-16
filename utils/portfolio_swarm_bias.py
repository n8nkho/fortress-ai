"""Portfolio-level swarm bias — reduce skim participation when infra is the edge sleeve."""
from __future__ import annotations

import os
from typing import Any


def portfolio_swarm_bias_enabled() -> bool:
    return str(os.environ.get("FORTRESS_PORTFOLIO_SWARM_BIAS", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def skim_wave_reduce_active() -> bool:
    if not portfolio_swarm_bias_enabled():
        return False
    try:
        from utils.swarm_session_si import load_session_policy

        skim = load_session_policy("skim_swarm")
        infra = load_session_policy("infra_swarm")
    except Exception:
        return False

    skim_mode = str(skim.get("mode") or "normal")
    if skim_mode not in ("tight", "churn", "critical"):
        return False
    if str(infra.get("mode") or "normal") != "normal":
        return False
    infra_exp = infra.get("session_expectancy_usd")
    if infra_exp is None or _f(infra_exp) <= 0:
        return False
    skim_exp = skim.get("session_expectancy_usd")
    if skim_exp is not None and _f(skim_exp) >= 0:
        return False
    return True


def filter_skim_wave_symbols(
    symbols: list[str],
    *,
    owned_symbols: set[str] | None = None,
) -> tuple[list[str], dict[str, Any] | None]:
    """When infra leads and skim is tight, scan fewer flat skim names (keep opens)."""
    if not skim_wave_reduce_active():
        return symbols, None

    owned = {str(s).upper() for s in (owned_symbols or set())}
    open_syms = [s for s in symbols if s.upper() in owned]
    flat_syms = [s for s in symbols if s.upper() not in owned]
    try:
        ratio = float(os.environ.get("FORTRESS_SKIM_WAVE_REDUCE_RATIO", "0.5") or 0.5)
    except ValueError:
        ratio = 0.5
    ratio = max(0.25, min(1.0, ratio))
    keep_n = max(1, int(len(flat_syms) * ratio)) if flat_syms else 0
    trimmed = open_syms + flat_syms[:keep_n]
    if len(trimmed) >= len(symbols):
        return symbols, None
    meta = {
        "portfolio_swarm_bias": True,
        "skim_wave_reduced": True,
        "before": len(symbols),
        "after": len(trimmed),
        "markers": ["portfolio_swarm_bias"],
    }
    return trimmed, meta
