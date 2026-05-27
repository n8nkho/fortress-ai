"""Shared swarm runtime helpers — universe refresh, open-position union."""
from __future__ import annotations

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


def wave_symbols(
    configured: list[str],
    positions: dict[str, dict[str, Any]],
    *,
    context: list[str] | None = None,
) -> list[str]:
    """Configured universe + any open broker positions (for exit management)."""
    merged: list[str] = []
    seen: set[str] = set()
    for sym in list(configured or []) + open_position_symbols(positions) + list(context or []):
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
