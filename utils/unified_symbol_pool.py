"""Eligible symbol pool for unified AI — off-denylist names only."""
from __future__ import annotations

import os
from typing import Any


_DEFAULT_ELIGIBLE = "QQQ,IWM,DIA,JPM,XOM,UNH,COST,WMT,DIS,BA"


def unified_eligible_universe(*, max_symbols: int = 12) -> list[str]:
    """Symbols unified AI may enter (excludes skim/infra denylist)."""
    from utils.skim_swarm_config import normalize_symbol, symbol_denylist_for_unified_ai

    deny = symbol_denylist_for_unified_ai()
    raw = (os.environ.get("FORTRESS_AI_ELIGIBLE_UNIVERSE") or _DEFAULT_ELIGIBLE).strip()
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        sym = normalize_symbol(part)
        if not sym or sym in deny or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= max_symbols:
            break
    return out


def filter_off_denylist(symbols: list[str], *, max_symbols: int = 12) -> list[str]:
    from utils.skim_swarm_config import normalize_symbol, symbol_denylist_for_unified_ai

    deny = symbol_denylist_for_unified_ai()
    out: list[str] = []
    seen: set[str] = set()
    for part in symbols:
        sym = normalize_symbol(str(part))
        if not sym or sym in deny or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= max_symbols:
            break
    return out


def build_unified_watchlist(*, max_symbols: int = 8) -> dict[str, Any]:
    """Classic screener candidates filtered to unified-eligible symbols."""
    eligible = unified_eligible_universe(max_symbols=max_symbols)
    try:
        from utils.classic_bridge import classic_screener_candidates

        scan = classic_screener_candidates(max_symbols=max_symbols * 2)
        classic = filter_off_denylist(scan.get("symbols") or [], max_symbols=max_symbols)
        if classic:
            return {
                "symbols": classic,
                "source": f"{scan.get('source') or 'classic'}_off_denylist",
                "eligible_fallback": eligible[:max_symbols],
            }
    except Exception:
        pass
    return {"symbols": eligible[:max_symbols], "source": "unified_eligible_default", "eligible_fallback": eligible[:max_symbols]}
