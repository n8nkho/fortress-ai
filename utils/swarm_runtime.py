"""Shared swarm runtime helpers — universe refresh, open-position union."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def open_position_symbols(positions: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for sym, pos in (positions or {}).items():
        if not isinstance(pos, dict):
            continue
        side = str(pos.get("side") or "flat").lower()
        if side in ("long", "short"):
            out.append(str(sym).upper())
    return out


def held_position_symbols(state_dir: str | Path) -> set[str]:
    """Symbols this swarm itself currently holds, per its own persisted symbol_state.

    Used to scope orphan-exit management to positions THIS swarm opened — a position
    opened by a sibling swarm on the same broker account has no local state here.
    """
    out: set[str] = set()
    p = Path(state_dir)
    if not p.exists():
        return out
    for f in p.glob("*.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(st, dict):
            continue
        if str(st.get("side") or "flat").lower() in ("long", "short"):
            sym = str(st.get("symbol") or f.stem.replace("_", ".")).upper()
            out.add(sym)
    return out


def wave_symbols(
    configured: list[str],
    positions: dict[str, dict[str, Any]],
    *,
    context: list[str] | None = None,
    owned_symbols: set[str] | None = None,
) -> list[str]:
    """Configured universe + open broker positions this swarm owns (for exit management).

    When ``owned_symbols`` is provided, only open broker positions in that set are
    unioned in. This prevents sibling swarms sharing one broker account from
    liquidating each other's positions (foreign positions are skipped). When None,
    all open positions are unioned (legacy behavior).
    """
    open_syms = open_position_symbols(positions)
    if owned_symbols is not None:
        owned_upper = {str(s).strip().upper() for s in owned_symbols}
        open_syms = [s for s in open_syms if s in owned_upper]
    merged: list[str] = []
    seen: set[str] = set()
    for sym in list(configured or []) + open_syms + list(context or []):
        s = str(sym or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        merged.append(s)
    return merged


def refresh_universe_if_changed(
    cached: list[str],
    universe_fn: Callable[[], list[str]],
) -> tuple[list[str], dict[str, Any] | None]:
    """Return updated symbol list when env/config universe drifts from cached boot list."""
    fresh = list(universe_fn() or [])
    if fresh == cached:
        return cached, None
    added = [s for s in fresh if s not in cached]
    removed = [s for s in cached if s not in fresh]
    return fresh, {
        "event": "universe_refresh",
        "previous": cached,
        "active": fresh,
        "added": added,
        "removed": removed,
    }
