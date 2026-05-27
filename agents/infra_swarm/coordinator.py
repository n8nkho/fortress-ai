"""Swarm-level risk: stack exposure, layer caps, daily stop."""
from __future__ import annotations

import threading
from typing import Any

from agents.infra_swarm.pnl import session_daily_realized_usd
from utils.infra_swarm_config import (
    daily_stop_usd,
    layer_for_symbol,
    max_l1_gross_long,
    max_open_positions,
    max_stack_long_per_layer,
    stack_halt_layers,
)


class EntrySlotGuard:
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


def layer_long_counts(positions: dict[str, dict[str, Any]]) -> dict[str, int]:
    out = {"L1": 0, "L2": 0, "L3": 0, "L4": 0}
    for sym, p in positions.items():
        if p.get("side") == "long" and int(p.get("qty") or 0) > 0:
            layer = layer_for_symbol(sym)
            out[layer] = out.get(layer, 0) + 1
    return out


def stack_aligned_long_layers(positions: dict[str, dict[str, Any]]) -> int:
    counts = layer_long_counts(positions)
    return sum(1 for v in counts.values() if v > 0)


def should_halt_new_entries(swarm: dict[str, Any], positions: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
    if swarm.get("halted"):
        return True, str(swarm.get("halt_reason") or "halted")
    try:
        from utils.swarm_session_si import session_halt_new_entries

        si_halt, si_reason = session_halt_new_entries("infra_swarm")
        if si_halt:
            return True, si_reason
    except Exception:
        pass
    pnl = session_daily_realized_usd()
    if pnl <= daily_stop_usd():
        return True, f"daily_stop:{pnl}"
    if stack_aligned_long_layers(positions) >= stack_halt_layers():
        return True, "stack_long_unwind"
    l1 = layer_long_counts(positions).get("L1", 0)
    try:
        from utils.swarm_session_si import effective_max_l1_gross

        l1_cap = effective_max_l1_gross("infra_swarm")
    except Exception:
        l1_cap = max_l1_gross_long()
    if l1 >= l1_cap:
        return True, "l1_gross_cap"
    return False, None


def layer_entry_blocked(symbol: str, positions: dict[str, dict[str, Any]], *, side: str) -> tuple[bool, str | None]:
    if side != "long":
        return False, None
    layer = layer_for_symbol(symbol)
    counts = layer_long_counts(positions)
    if counts.get(layer, 0) >= max_stack_long_per_layer():
        return True, f"layer_cap:{layer}"
    if layer == "L1":
        try:
            from utils.swarm_session_si import effective_max_l1_gross

            l1_cap = effective_max_l1_gross("infra_swarm")
        except Exception:
            l1_cap = max_l1_gross_long()
        if counts.get("L1", 0) >= l1_cap:
            return True, "l1_gross_cap"
    return False, None


def apply_daily_pnl(swarm: dict[str, Any], delta: float) -> dict[str, Any]:
    del delta
    pnl = session_daily_realized_usd()
    swarm["day_realized_pnl"] = round(pnl, 4)
    if pnl <= daily_stop_usd():
        swarm["halted"] = True
        swarm["halt_reason"] = f"daily_stop:{pnl}"
    return swarm


def max_open_ok(open_n: int) -> bool:
    try:
        from utils.swarm_session_si import effective_max_open

        cap = effective_max_open("infra_swarm")
    except Exception:
        cap = max_open_positions()
    return open_n < cap
