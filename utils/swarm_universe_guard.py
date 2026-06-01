"""Swarm universe guards — block orphan entries, purge stale symbol state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _swarm_dir(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name


def configured_universe(component: str) -> list[str]:
    if component == "skim_swarm":
        from utils.skim_swarm_config import universe

        return list(universe() or [])
    if component == "infra_swarm":
        from utils.infra_swarm_config import universe

        return list(universe() or [])
    return []


def wave_context_symbols(component: str) -> frozenset[str]:
    """Bar-context symbols unioned into waves but not env-universe members."""
    if component == "skim_swarm":
        return frozenset({"SPY", "SOXX"})
    if component == "infra_swarm":
        from utils.infra_swarm_config import anchor_symbol

        return frozenset({anchor_symbol(), "SPY"})
    return frozenset()


def entry_blocked_outside_universe(component: str, symbol: str) -> tuple[bool, str | None]:
    """Block new entries on symbols outside configured universe (exit-only orphans)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return True, "invalid_symbol"
    allowed = {s.upper() for s in configured_universe(component)}
    if sym in allowed:
        return False, None
    return True, "orphan_symbol_outside_universe"


def purge_orphan_symbol_states(component: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Remove flat state files for symbols outside configured universe."""
    allowed = {s.upper() for s in configured_universe(component)}
    swarm = _swarm_dir(component)
    removed: list[str] = []
    kept_open: list[str] = []

    for sub in ("state",):
        d = swarm / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.json"):
            sym = f.stem.replace("_", ".").upper()
            if sym in allowed:
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                doc = {}
            side = str(doc.get("side") or "flat").lower()
            if side in ("long", "short"):
                kept_open.append(sym)
                continue
            if not dry_run:
                try:
                    f.unlink()
                except OSError:
                    pass
            removed.append(sym)

    return {
        "component": component,
        "allowed_count": len(allowed),
        "removed_flat": sorted(set(removed)),
        "kept_open_orphans": sorted(set(kept_open)),
        "dry_run": dry_run,
    }
