"""Order sizing helpers — chunk large exits under FORTRESS_MAX_ORDER_NOTIONAL_USD."""
from __future__ import annotations

from typing import Any

from utils.order_chunking import chunk_qtys, max_order_notional_usd


def chunk_exit_order(
    symbol: str,
    total_qty: int,
    max_notional: float | None = None,
    *,
    px: float,
    side: str = "SELL",
) -> list[dict[str, Any]]:
    """
    Split an exit into child orders where each chunk notional <= max_notional.

    Uses FORTRESS_MAX_ORDER_NOTIONAL_USD when max_notional is omitted.
    """
    sym = (symbol or "").strip().upper()
    qty = int(abs(total_qty or 0))
    if not sym or qty <= 0:
        return []

    price = float(px or 0)
    cap = (
        float(max_notional)
        if max_notional is not None and float(max_notional) > 0
        else max_order_notional_usd(side=side, portfolio_equity_usd=None)
    )
    if price <= 0:
        return [{"symbol": sym, "side": side, "qty": qty, "px": price}]

    order_qtys = chunk_qtys(qty, px=price, max_notional_usd=cap)
    return [{"symbol": sym, "side": side, "qty": q, "px": price} for q in order_qtys]


def chunk_qtys_for_exit(total_qty: int, *, px: float, max_notional_usd: float) -> list[int]:
    """Backward-compatible qty-only chunking."""
    return chunk_qtys(total_qty, px=px, max_notional_usd=max_notional_usd)


__all__ = ["chunk_exit_order", "chunk_qtys_for_exit", "max_order_notional_usd"]
