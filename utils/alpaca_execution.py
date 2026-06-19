"""Alpaca execution helpers — quotes, passive limits, bracket OCO exits."""
from __future__ import annotations

import logging
from typing import Any

from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs
from utils.edge_quality import bracket_prices, clamp_bracket_prices
from utils.edge_quality_config import bracket_exits_enabled, passive_entry_enabled

logger = logging.getLogger("alpaca_execution")


def trading_client():
    key, sec = alpaca_credentials()
    if not key or not sec:
        return None
    try:
        from alpaca.trading.client import TradingClient

        return TradingClient(key, sec, **alpaca_trading_client_kwargs())
    except ImportError:
        return None


def fetch_quote(symbol: str) -> dict[str, float | None]:
    """Best-effort bid/ask from Alpaca latest quote."""
    sym = str(symbol or "").upper()
    tc = trading_client()
    if not tc:
        return {"bid": None, "ask": None, "last": None}
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        key, sec = alpaca_credentials()
        dc = StockHistoricalDataClient(key, sec)
        q = dc.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=sym))
        row = q.get(sym) if isinstance(q, dict) else getattr(q, sym, None)
        if row is None:
            return {"bid": None, "ask": None, "last": None}
        bid = float(getattr(row, "bid_price", 0) or 0) or None
        ask = float(getattr(row, "ask_price", 0) or 0) or None
        last = None
        if bid and ask:
            last = (bid + ask) / 2.0
        return {"bid": bid, "ask": ask, "last": last}
    except Exception as e:
        logger.debug("quote fetch failed %s: %s", sym, e)
        return {"bid": None, "ask": None, "last": None}


def passive_limit_price(*, side: str, quote: dict[str, float | None], fallback: float) -> float:
    """Price limit at passive side of book when quote available."""
    side = side.upper()
    bid = quote.get("bid")
    ask = quote.get("ask")
    if side == "BUY":
        if bid and bid > 0:
            return round(bid, 2)
        return round(fallback * 0.9998, 2)
    if ask and ask > 0:
        return round(ask, 2)
    return round(fallback * 1.0002, 2)


def has_open_exit_order(symbol: str, *, side: str = "SELL") -> bool:
    """True when an open order already exists for this symbol/side (prevents exit spam)."""
    tc = trading_client()
    if not tc:
        return False
    sym = str(symbol or "").upper()
    want = str(side or "SELL").lower()
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = tc.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym], limit=20)
        )
        for o in orders or []:
            o_side = str(getattr(getattr(o, "side", ""), "value", getattr(o, "side", ""))).lower()
            if o_side == want:
                return True
    except Exception as e:
        logger.debug("open exit check %s: %s", sym, e)
    return False


def cancel_open_orders(symbol: str) -> int:
    tc = trading_client()
    if not tc:
        return 0
    sym = str(symbol or "").upper()
    cancelled = 0
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = tc.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym], nested=True)
        )
        for o in orders or []:
            try:
                tc.cancel_order_by_id(o.id)
                cancelled += 1
            except Exception:
                continue
    except Exception as e:
        logger.debug("cancel orders %s: %s", sym, e)
    return cancelled


def submit_entry_with_bracket(
    *,
    symbol: str,
    side: str,
    qty: int,
    entry_price: float,
    target_usd: float,
    stop_usd: float,
) -> dict[str, Any]:
    """Submit bracket entry (passive limit when enabled) with OCO exit at broker."""
    tc = trading_client()
    if not tc:
        return {"executed": False, "detail": "alpaca_not_configured", "block_reason": "alpaca_not_configured"}

    sym = str(symbol or "").upper()
    side_u = side.upper()
    alpaca_side = "buy" if side_u == "BUY" else "sell"

    quote = fetch_quote(sym)
    use_bracket = bracket_exits_enabled()
    use_passive = passive_entry_enabled()

    base_px = float(entry_price)
    if use_passive:
        base_px = passive_limit_price(side=side_u, quote=quote, fallback=entry_price)

    tp_px, sl_px = bracket_prices(
        side="long" if side_u == "BUY" else "short",
        entry_price=base_px,
        target_usd=target_usd,
        stop_usd=abs(stop_usd),
    )
    tp_px, sl_px = clamp_bracket_prices(
        side="long" if side_u == "BUY" else "short",
        base_price=base_px,
        take_profit=tp_px,
        stop_loss=sl_px,
    )

    try:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        order_side = OrderSide.BUY if alpaca_side == "buy" else OrderSide.SELL
        tp_req = TakeProfitRequest(limit_price=tp_px)
        sl_req = StopLossRequest(stop_price=sl_px)

        if use_bracket and use_passive:
            limit_px = base_px
            req = LimitOrderRequest(
                symbol=sym,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_px,
                order_class=OrderClass.BRACKET,
                take_profit=tp_req,
                stop_loss=sl_req,
            )
            order_type = "bracket_limit"
        elif use_bracket:
            req = MarketOrderRequest(
                symbol=sym,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=tp_req,
                stop_loss=sl_req,
            )
            order_type = "bracket_market"
        elif use_passive:
            limit_px = base_px
            req = LimitOrderRequest(
                symbol=sym,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_px,
            )
            order_type = "limit"
        else:
            req = MarketOrderRequest(
                symbol=sym,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,
            )
            order_type = "market"

        order = tc.submit_order(req)
        detail = {
            "id": str(order.id),
            "status": str(order.status),
            "side": side_u,
            "qty": qty,
            "order_type": order_type,
            "take_profit_price": tp_px if use_bracket else None,
            "stop_loss_price": sl_px if use_bracket else None,
        }
        return {"executed": True, "detail": detail, "block_reason": "executed"}
    except Exception as e:
        logger.warning("bracket submit failed %s: %s — falling back to market", sym, e)
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest

            order = tc.submit_order(
                MarketOrderRequest(
                    symbol=sym,
                    qty=qty,
                    side=OrderSide.BUY if alpaca_side == "buy" else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
            )
            return {
                "executed": True,
                "detail": {
                    "id": str(order.id),
                    "status": str(order.status),
                    "side": side_u,
                    "qty": qty,
                    "order_type": "market_fallback",
                    "bracket_error": str(e)[:120],
                },
                "block_reason": "executed",
            }
        except Exception as e2:
            return {
                "executed": False,
                "detail": f"broker_error:{type(e2).__name__}:{e2}",
                "block_reason": "broker_error",
            }
