"""Execute ladder actions via Alpaca with exposure caps and pre_trade_gate."""
from __future__ import annotations

from typing import Any

import yfinance as yf

from agents.spy_intraday.eod import is_force_flatten_window
from agents.spy_intraday.ladder import (
    can_add_rung,
    load_ladder_state,
    record_rung,
    save_ladder_state,
    shares_for_rung,
)
from agents.spy_intraday.observe import _alpaca_client
from utils.pre_trade_gate import evaluate_pre_trade_submission, format_gate_block_message
from utils.spy_agent_config import dry_run, index_symbol, max_exposure_usd, min_confidence


def _last_price(sym: str) -> float | None:
    try:
        t = yf.Ticker(sym)
        return float(t.fast_info.get("last_price") or t.history(period="1d")["Close"].iloc[-1])
    except Exception:
        return None


def _position_qty_side(tc: Any, sym: str) -> tuple[int, str]:
    try:
        for p in tc.get_all_positions():
            if str(getattr(p, "symbol", "")).upper() == sym:
                qty = float(getattr(p, "qty", 0) or 0)
                if qty > 0:
                    return int(qty), "long"
                if qty < 0:
                    return int(abs(qty)), "short"
    except Exception:
        pass
    return 0, "flat"


def act(decision: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    action = (decision.get("action") or "wait").lower()
    sym = index_symbol()
    conf = float(decision.get("confidence") or 0.0)
    result: dict[str, Any] = {"action": action, "executed": False, "detail": None}

    if action == "wait":
        result["detail"] = "no broker action"
        return result

    if dry_run():
        result["detail"] = "dry_run_blocked"
        return result

    if conf < min_confidence() and action not in ("flatten_all",):
        result["detail"] = f"confidence_below_threshold:{conf}<{min_confidence()}"
        return result

    tc = _alpaca_client()
    if not tc:
        result["detail"] = "alpaca_not_configured"
        return result

    px = _last_price(sym)
    if not px or px <= 0:
        result["detail"] = "no_price"
        return result

    equity = float(observation.get("equity") or 0)
    exposure = float(observation.get("exposure_usd") or 0)
    ladder = load_ladder_state()
    pos_qty, pos_side = _position_qty_side(tc, sym)

    def _submit(side: str, qty: int) -> dict[str, Any]:
        if qty <= 0:
            return {"executed": False, "detail": "zero_qty"}
        est = qty * px
        if side == "BUY" and exposure + est > max_exposure_usd() * 1.02:
            return {"executed": False, "detail": f"max_exposure_exceeded:{exposure}+{est}>{max_exposure_usd()}"}
        gate = evaluate_pre_trade_submission(
            side=side,
            symbol=sym,
            qty=float(qty),
            estimated_notional_usd=est,
            portfolio_equity_usd=equity if equity > 0 else None,
            order_class="equity",
            bid=px * 0.9985,
            ask=px * 1.0015,
            quote_age_seconds=45.0,
        )
        if not gate["allowed"]:
            return {"executed": False, "detail": format_gate_block_message(gate)}
        try:
            from alpaca.trading.requests import MarketOrderRequest

            order = tc.submit_order(
                MarketOrderRequest(symbol=sym, qty=qty, side=side, time_in_force="day")
            )
            return {
                "executed": True,
                "detail": {"id": str(order.id), "status": str(order.status), "side": side, "qty": qty},
            }
        except Exception as e:
            return {"executed": False, "detail": f"broker_error:{type(e).__name__}:{e}"}

    if action == "flatten_all" or (is_force_flatten_window() and pos_qty > 0):
        if pos_qty <= 0:
            ladder = record_rung(ladder, side="flat", qty=0, price=px, action="flatten_all")
            save_ladder_state(ladder)
            result["detail"] = "already_flat"
            return result
        side = "SELL" if pos_side == "long" else "BUY"
        out = _submit(side, pos_qty)
        result.update(out)
        if out.get("executed"):
            ladder = record_rung(ladder, side=pos_side, qty=pos_qty, price=px, action="flatten_all")
            save_ladder_state(ladder)
        return result

    if action in ("add_long", "add_short"):
        side_name = "long" if action == "add_long" else "short"
        if not can_add_rung(ladder, side_name):
            result["detail"] = "ladder_full_or_side_mismatch"
            return result
        qty = shares_for_rung(px)
        broker_side = "BUY" if action == "add_long" else "SELL"
        if pos_side == "short" and action == "add_long":
            result["detail"] = "must_flatten_short_first"
            return result
        if pos_side == "long" and action == "add_short":
            result["detail"] = "must_flatten_long_first"
            return result
        out = _submit(broker_side, qty)
        result.update(out)
        if out.get("executed"):
            ladder = record_rung(ladder, side=side_name, qty=qty, price=px, action=action)
            save_ladder_state(ladder)
        return result

    if action == "trim":
        if pos_qty <= 0:
            result["detail"] = "no_position_to_trim"
            return result
        qty = min(shares_for_rung(px), pos_qty)
        side = "SELL" if pos_side == "long" else "BUY"
        out = _submit(side, qty)
        result.update(out)
        if out.get("executed"):
            ladder = record_rung(ladder, side=pos_side, qty=qty, price=px, action="trim")
            save_ladder_state(ladder)
        return result

    result["detail"] = "unknown_action"
    return result
