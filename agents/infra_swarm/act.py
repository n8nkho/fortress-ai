"""Execute skim actions — max 1 share per symbol on entry; exits sized to position."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import yfinance as yf

from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs
from utils.order_chunking import chunk_qtys, max_order_notional_usd
from utils.pre_trade_gate import evaluate_pre_trade_submission, format_gate_block_message
from utils.infra_swarm_config import dry_run, max_shares, normalize_symbol


def _alpaca_client():
    key, sec = alpaca_credentials()
    if not key or not sec:
        return None
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return None
    return TradingClient(key, sec, **alpaca_trading_client_kwargs())


def _last_price(sym: str) -> float | None:
    try:
        t = yf.Ticker(sym)
        return float(t.fast_info.get("last_price") or t.history(period="1d")["Close"].iloc[-1])
    except Exception:
        return None


def _broker_symbol(sym: str) -> str:
    """Alpaca uses BRK.B style."""
    return normalize_symbol(sym)


def act(
    decision: dict[str, Any],
    *,
    symbol: str,
    equity: float,
    position: dict[str, Any] | None,
) -> dict[str, Any]:
    action = (decision.get("action") or "wait").lower()
    sym = _broker_symbol(symbol)
    result: dict[str, Any] = {"action": action, "executed": False, "detail": None, "block_reason": None}

    if action == "wait":
        result["detail"] = "no broker action"
        result["block_reason"] = decision.get("reasoning")
        return result

    if dry_run():
        result["detail"] = "dry_run_blocked"
        result["block_reason"] = "dry_run_blocked"
        return result

    tc = _alpaca_client()
    if not tc:
        result["detail"] = "alpaca_not_configured"
        result["block_reason"] = "alpaca_not_configured"
        return result

    px = _last_price(sym)
    if not px or px <= 0:
        result["detail"] = "no_price"
        result["block_reason"] = "no_price"
        return result

    pos = position or {}
    pos_side = pos.get("side") or "flat"
    pos_qty = int(pos.get("qty") or 0)
    qty_cap = max_shares()

    def _submit_one(side: str, qty: int, *, is_exit: bool) -> dict[str, Any]:
        if qty <= 0:
            return {"executed": False, "detail": "qty_invalid", "block_reason": "qty_invalid"}
        if not is_exit and qty > qty_cap:
            return {"executed": False, "detail": "qty_invalid", "block_reason": "qty_invalid"}
        if is_exit and qty > pos_qty:
            return {"executed": False, "detail": "qty_invalid", "block_reason": "qty_invalid"}
        est = qty * px
        gate = evaluate_pre_trade_submission(
            side=side,
            symbol=sym,
            qty=float(qty),
            estimated_notional_usd=est,
            portfolio_equity_usd=equity if equity > 0 else None,
            order_class="equity",
            bid=px * 0.9985,
            ask=px * 1.0015,
            quote_age_seconds=30.0,
        )
        if not gate["allowed"]:
            msg = format_gate_block_message(gate)
            return {"executed": False, "detail": msg, "block_reason": msg.split(":")[0]}
        try:
            from alpaca.trading.requests import MarketOrderRequest

            alpaca_side = "buy" if side == "BUY" else "sell" if side == "SELL" else str(side).lower()
            order = tc.submit_order(
                MarketOrderRequest(symbol=sym, qty=qty, side=alpaca_side, time_in_force="day")
            )
            return {
                "executed": True,
                "detail": {"id": str(order.id), "status": str(order.status), "side": side, "qty": qty},
                "block_reason": "executed",
            }
        except Exception as e:
            return {
                "executed": False,
                "detail": f"broker_error:{type(e).__name__}:{e}",
                "block_reason": "broker_error",
            }

    def _submit_exit(side: str, total_qty: int) -> dict[str, Any]:
        max_notional = max_order_notional_usd(side=side, portfolio_equity_usd=equity if equity > 0 else None)
        chunks = chunk_qtys(total_qty, px=float(px), max_notional_usd=max_notional)
        submitted: list[dict[str, Any]] = []
        for q in chunks:
            r = _submit_one(side, q, is_exit=True)
            if not r.get("executed"):
                if submitted:
                    return {
                        "executed": True,
                        "partial": True,
                        "detail": {"orders": submitted, "stopped_at": r.get("detail")},
                        "block_reason": "partial_exit",
                    }
                return r
            submitted.append(r["detail"])
        if len(submitted) == 1:
            return {"executed": True, "detail": submitted[0], "block_reason": "executed"}
        return {
            "executed": True,
            "chunked_exit": True,
            "detail": {"orders": submitted},
            "block_reason": "executed",
        }

    if action == "flatten":
        if pos_qty <= 0:
            result["detail"] = "already_flat"
            result["block_reason"] = "already_flat"
            return result
        side = "SELL" if pos_side == "long" else "BUY"
        result.update(_submit_exit(side, pos_qty))
        return result

    if action == "exit_position":
        if pos_qty <= 0:
            result["detail"] = "no_position"
            result["block_reason"] = "no_position"
            return result
        side = "SELL" if pos_side == "long" else "BUY"
        result.update(_submit_exit(side, pos_qty))
        return result

    if action == "enter_long":
        if pos_side != "flat":
            result["detail"] = f"not_flat:{pos_side}"
            result["block_reason"] = "not_flat"
            return result
        result.update(_submit_one("BUY", qty_cap, is_exit=False))
        return result

    if action == "enter_short":
        if pos_side != "flat":
            result["detail"] = f"not_flat:{pos_side}"
            result["block_reason"] = "not_flat"
            return result
        result.update(_submit_one("SELL", qty_cap, is_exit=False))
        return result

    result["detail"] = "unknown_action"
    result["block_reason"] = "unknown_action"
    return result


def cooldown_until_seconds(sec: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=sec)).isoformat()
