"""Poll Alpaca orders until filled, terminal, or timeout."""
from __future__ import annotations

import time
from typing import Any


def _filled_qty(order: Any) -> int:
    try:
        return int(getattr(order, "filled_qty", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _status(order: Any) -> str:
    st = getattr(order, "status", None)
    return str(getattr(st, "value", st) or "").lower()


def poll_order_fill(
    trading_client: Any,
    order_id: str,
    *,
    timeout_sec: float = 45.0,
    poll_interval_sec: float = 0.75,
) -> dict[str, Any]:
    """Return fill summary; filled_qty=0 when order did not fill in time."""
    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    last = None
    while time.monotonic() < deadline:
        try:
            last = trading_client.get_order_by_id(order_id)
        except Exception as e:
            return {"ok": False, "order_id": order_id, "error": f"{type(e).__name__}:{e}", "filled_qty": 0}
        st = _status(last)
        fq = _filled_qty(last)
        if fq > 0 or st in ("filled", "partially_filled"):
            avg = getattr(last, "filled_avg_price", None)
            return {
                "ok": True,
                "order_id": order_id,
                "status": st,
                "filled_qty": fq,
                "filled_avg_price": float(avg) if avg is not None else None,
            }
        if st in ("canceled", "cancelled", "expired", "rejected", "replaced"):
            return {
                "ok": False,
                "order_id": order_id,
                "status": st,
                "filled_qty": fq,
                "terminal": True,
            }
        time.sleep(poll_interval_sec)
    st = _status(last) if last is not None else "unknown"
    return {
        "ok": False,
        "order_id": order_id,
        "status": st,
        "filled_qty": _filled_qty(last) if last is not None else 0,
        "timeout": True,
    }
