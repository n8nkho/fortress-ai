"""Swarm-level risk: open count, daily stop, semi correlation."""
from __future__ import annotations

import threading
from typing import Any

from agents.skim_swarm.pnl import session_daily_realized_usd
from utils.skim_swarm_config import daily_stop_usd, max_open_positions, semi_symbols


class EntrySlotGuard:
    """Atomic reservation so parallel workers cannot exceed max_open."""

    def __init__(self, open_count: int, max_open: int) -> None:
        self._lock = threading.Lock()
        self._open_count = max(0, int(open_count))
        self._max_open = max(0, int(max_open))
        self._reserved = 0

    def try_reserve(self) -> bool:
        with self._lock:
            if self._max_open <= 0:
                return False
            if self._open_count + self._reserved >= self._max_open:
                return False
            self._reserved += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._reserved > 0:
                self._reserved -= 1


def count_open(positions: dict[str, dict[str, Any]]) -> int:
    n = 0
    for p in positions.values():
        if (p.get("side") or "flat") != "flat" and int(p.get("qty") or 0) > 0:
            n += 1
    return n


def semi_long_stack(positions: dict[str, dict[str, Any]]) -> int:
    n = 0
    for sym, p in positions.items():
        if sym in semi_symbols() and p.get("side") == "long":
            n += 1
    return n


def should_halt_new_entries(swarm: dict[str, Any], positions: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
    if swarm.get("halted"):
        return True, str(swarm.get("halt_reason") or "halted")
    try:
        from utils.swarm_session_si import session_halt_new_entries

        si_halt, si_reason = session_halt_new_entries("skim_swarm")
        if si_halt:
            return True, si_reason
    except Exception:
        pass
    pnl = session_daily_realized_usd()
    if pnl <= daily_stop_usd():
        return True, f"daily_stop:{pnl}"
    if semi_long_stack(positions) >= 4:
        return True, "semi_long_stack"
    return False, None


def apply_daily_pnl(swarm: dict[str, Any], delta: float) -> dict[str, Any]:
    """Sync halt state from authoritative decisions-log session P&L."""
    del delta  # wave delta kept for logging; halt uses full session total
    pnl = session_daily_realized_usd()
    swarm["day_realized_pnl"] = round(pnl, 4)
    if pnl <= daily_stop_usd():
        swarm["halted"] = True
        swarm["halt_reason"] = f"daily_stop:{pnl}"
    return swarm


def max_open_ok(open_n: int) -> bool:
    try:
        from utils.swarm_session_si import effective_max_open

        cap = effective_max_open("skim_swarm")
    except Exception:
        cap = max_open_positions()
    return open_n < cap
