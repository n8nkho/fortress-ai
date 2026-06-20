"""Cancel stale / phantom open Alpaca orders blocking exits."""
from __future__ import annotations

from datetime import datetime, timezone
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


def _order_side(o: Any) -> str:
    return str(getattr(getattr(o, "side", ""), "value", getattr(o, "side", ""))).lower()


def _order_filled_qty(o: Any) -> int:
    try:
        return int(float(getattr(o, "filled_qty", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _order_age_minutes(o: Any, *, now: datetime | None = None) -> float | None:
    raw = getattr(o, "submitted_at", None) or getattr(o, "created_at", None)
    if raw is None:
        return None
    try:
        if isinstance(raw, datetime):
            submitted = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        else:
            submitted = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - submitted).total_seconds() / 60.0)


def cancel_stale_open_orders(
    trading_client: Any,
    *,
    cancel_phantom_sells: bool = True,
    cancel_duplicate_sells: bool = True,
    stale_sell_minutes: float = 30.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Cancel open SELL orders that block reconciliation:
    - sells for symbols with zero broker position (phantom) — always cancel
    - sells for held symbols open > stale_sell_minutes without fill
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
        if _order_side(o) != "sell":
            continue
        by_sym.setdefault(sym, []).append(o)

    to_cancel: list[Any] = []
    cancel_ids: set[str] = set()
    cancel_reasons: dict[str, str] = {}

    def _queue(o: Any, reason: str) -> None:
        oid = str(getattr(o, "id", ""))
        if oid and oid not in cancel_ids:
            cancel_ids.add(oid)
            to_cancel.append(o)
            cancel_reasons[oid] = reason

    for sym, orders in by_sym.items():
        if cancel_phantom_sells and held.get(sym, 0) <= 0:
            for o in orders:
                _queue(o, "phantom_no_position")
            continue
        for o in orders:
            if _order_filled_qty(o) > 0:
                continue
            age = _order_age_minutes(o)
            if age is not None and age > float(stale_sell_minutes):
                _queue(o, f"stale_{int(age)}m")
        if cancel_duplicate_sells and len(orders) > 1:
            sorted_orders = sorted(
                orders,
                key=lambda x: getattr(x, "submitted_at", None) or "",
                reverse=True,
            )
            for o in sorted_orders[1:]:
                _queue(o, "duplicate_sell")

    cancelled: list[str] = []
    errors: list[str] = []
    if dry_run:
        return {
            "dry_run": True,
            "open_sells": sum(len(v) for v in by_sym.values()),
            "would_cancel": len(to_cancel),
            "symbols": sorted({str(getattr(o, "symbol", "")).upper() for o in to_cancel}),
            "held_positions": held,
            "cancel_reasons": cancel_reasons,
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
        "cancel_reasons": {k: cancel_reasons[k] for k in cancelled if k in cancel_reasons},
    }


def run_live_hygiene(*, dry_run: bool = False) -> dict[str, Any]:
    from pathlib import Path

    try:
        from utils.env_load import load_fortress_dotenv

        load_fortress_dotenv(Path(__file__).resolve().parent.parent)
    except Exception:
        pass
    from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs

    key, sec = alpaca_credentials()
    if not key or not sec:
        return {"ok": False, "error": "missing_alpaca_credentials"}
    from alpaca.trading.client import TradingClient

    tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
    return cancel_stale_open_orders(tc, dry_run=dry_run, stale_sell_minutes=30.0)


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Cancel stale open Alpaca SELL orders")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run_live_hygiene(dry_run=args.dry_run), indent=2, default=str))
