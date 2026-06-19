"""Cancel stale / phantom open Alpaca orders blocking exits."""
from __future__ import annotations

from typing import Any


def _broker_position_qty(trading_client: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in trading_client.get_all_positions():
        sym = str(getattr(p, "symbol", "")).upper()
        try:
            qty = int(float(getattr(p, "qty", 0) or 0))
        except (TypeError, ValueError):
            qty = 0
        if sym and qty != 0:
            out[sym] = abs(qty)
    return out


def cancel_stale_open_orders(
    trading_client: Any,
    *,
    cancel_phantom_sells: bool = True,
    cancel_duplicate_sells: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Cancel open SELL orders that block reconciliation:
    - sells for symbols with zero broker position (phantom)
    - duplicate sells per symbol (keep newest, cancel older)
    """
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    held = _broker_position_qty(trading_client)
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
    open_orders = list(trading_client.get_orders(filter=req) or [])

    by_sym: dict[str, list[Any]] = {}
    for o in open_orders:
        sym = str(getattr(o, "symbol", "")).upper()
        side = str(getattr(getattr(o, "side", ""), "value", getattr(o, "side", ""))).lower()
        if side != "sell":
            continue
        by_sym.setdefault(sym, []).append(o)

    to_cancel: list[Any] = []
    for sym, orders in by_sym.items():
        if cancel_phantom_sells and held.get(sym, 0) <= 0:
            to_cancel.extend(orders)
            continue
        if cancel_duplicate_sells and len(orders) > 1:
            sorted_orders = sorted(
                orders,
                key=lambda x: getattr(x, "submitted_at", None) or "",
                reverse=True,
            )
            to_cancel.extend(sorted_orders[1:])

    cancelled: list[str] = []
    errors: list[str] = []
    if dry_run:
        return {
            "dry_run": True,
            "open_sells": sum(len(v) for v in by_sym.values()),
            "would_cancel": len(to_cancel),
            "symbols": sorted({str(getattr(o, "symbol", "")).upper() for o in to_cancel}),
            "held_positions": held,
        }

    for o in to_cancel:
        oid = str(getattr(o, "id", ""))
        if not oid:
            continue
        try:
            trading_client.cancel_order_by_id(oid)
            cancelled.append(oid)
        except Exception as e:
            errors.append(f"{oid}:{type(e).__name__}:{e}")

    return {
        "ok": True,
        "open_sells": sum(len(v) for v in by_sym.values()),
        "cancelled_count": len(cancelled),
        "cancelled_ids": cancelled[:40],
        "errors": errors[:20],
        "held_positions": held,
    }
