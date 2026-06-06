"""Append swarm exit fills to the unified Fortress AI pnl ledger."""
from __future__ import annotations

from typing import Any

from utils.ai_pnl_ledger import append_realized_fill


def record_swarm_exit(
    component: str,
    *,
    symbol: str,
    pnl_usd: float,
    side: str,
    qty: int,
    order_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {"component": component, "action": "exit_position", **(extra or {})}
    return append_realized_fill(
        symbol=symbol,
        pnl_usd=pnl_usd,
        side=side,
        qty=qty,
        order_id=order_id,
        source=component,
        extra=meta,
    )
