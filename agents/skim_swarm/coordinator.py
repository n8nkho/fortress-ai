"""Swarm-level risk: open count, daily stop, semi correlation."""
from __future__ import annotations

from typing import Any

from utils.skim_swarm_config import daily_stop_usd, max_open_positions, semi_symbols


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
    pnl = float(swarm.get("day_realized_pnl") or 0)
    if pnl <= daily_stop_usd():
        return True, f"daily_stop:{pnl}"
    if semi_long_stack(positions) >= 4:
        return True, "semi_long_stack"
    return False, None


def apply_daily_pnl(swarm: dict[str, Any], delta: float) -> dict[str, Any]:
    swarm["day_realized_pnl"] = round(float(swarm.get("day_realized_pnl") or 0) + float(delta), 4)
    if swarm["day_realized_pnl"] <= daily_stop_usd():
        swarm["halted"] = True
        swarm["halt_reason"] = f"daily_stop:{swarm['day_realized_pnl']}"
    return swarm


def max_open_ok(open_n: int) -> bool:
    return open_n < max_open_positions()
