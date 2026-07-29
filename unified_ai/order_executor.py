"""Unified AI order execution — chunked exits under notional caps."""
from __future__ import annotations

import logging
import time
from typing import Any

from unified_ai.config import FORTRESS_MAX_ORDER_NOTIONAL_USD
from unified_ai.order_utils import chunk_exit_orders
from unified_ai.settings import max_order_notional_usd
from utils.order_chunking import CHUNK_DELAY_MIN_SEC, held_qty_for_symbol

log = logging.getLogger(__name__)


def chunk_exit_order(
    symbol: str,
    total_notional: float,
    *,
    px: float,
    max_notional: float | None = None,
) -> dict[str, Any]:
    """
    Split total_notional into child exit orders each <= FORTRESS_MAX_ORDER_NOTIONAL_USD.

    Returns order_qtys and chunked_exit metadata (notional converted via px).
    """
    sym = (symbol or "").strip().upper()
    result: dict[str, Any] = {
        "symbol": sym,
        "order_qtys": [],
        "order_notionals": [],
        "chunked_exit": False,
    }
    total = abs(float(total_notional or 0))
    if total <= 0:
        result["block_reason"] = "invalid_notional"
        return result

    price = float(px or 0)
    if price <= 0:
        result["block_reason"] = "no_price"
        return result

    cap = (
        float(max_notional)
        if max_notional is not None and float(max_notional) > 0
        else max_order_notional_usd(side="SELL", portfolio_equity_usd=None)
    )
    result["max_notional_usd"] = cap
    result["total_notional_usd"] = total

    total_qty = max(1, int(total / price))
    order_qtys = chunk_exit_orders(sym, total_qty, cap, px=price)
    if not order_qtys:
        result["block_reason"] = "invalid_chunk_qty"
        return result

    order_notionals = [q * price for q in order_qtys]
    if len(order_qtys) > 1:
        result["chunked_exit"] = True
        result["chunk_count"] = len(order_qtys)
        log.info(
            "chunked_exit:%s notional=%.2f cap=%.2f chunks=%d",
            sym,
            total,
            cap,
            len(order_qtys),
        )

    result["order_qtys"] = order_qtys
    result["order_notionals"] = order_notionals
    return result


class OrderExecutor:
    def __init__(self, positions: list[dict[str, Any]] | None = None) -> None:
        self._positions = positions or []

    def exit_position(
        self,
        symbol: str,
        qty: int,
        *,
        px: float,
        equity: float = 0.0,
        side: str = "SELL",
        max_chunk_size: float | None = None,
    ) -> dict[str, Any]:
        """
        Plan exit order chunks when position notional exceeds FORTRESS_MAX_ORDER_NOTIONAL_USD.

        Returns dict with order_qtys, chunked_exit flag, and optional clamp metadata.
        """
        sym = (symbol or "").strip().upper()
        held_qty = held_qty_for_symbol(self._positions, sym)
        result: dict[str, Any] = {"symbol": sym, "order_qtys": [], "chunked_exit": False}

        if held_qty <= 0:
            result["block_reason"] = "no_position"
            result["detail"] = f"no_position:{sym}"
            return result

        exit_qty = int(abs(qty or 0))
        if exit_qty <= 0:
            result["block_reason"] = "invalid_symbol_or_qty"
            return result
        if exit_qty > held_qty:
            exit_qty = held_qty
            result["qty_clamped_to_position"] = True

        if px <= 0:
            result["order_qtys"] = [exit_qty]
            return result

        total_notional = exit_qty * float(px)
        cap = (
            float(max_chunk_size)
            if max_chunk_size is not None and float(max_chunk_size) > 0
            else max_order_notional_usd(side=side, portfolio_equity_usd=equity if equity > 0 else None)
        )
        result["max_notional_usd"] = cap
        result["total_notional_usd"] = total_notional

        order_qtys = chunk_exit_orders(sym, exit_qty, cap, px=float(px))
        if not order_qtys:
            result["block_reason"] = "invalid_chunk_qty"
            result["detail"] = "invalid_chunk_qty"
            return result

        if len(order_qtys) > 1:
            result["chunked_exit"] = True
            result["chunk_count"] = len(order_qtys)
            log.info(
                "chunked_exit:%s notional=%.2f cap=%.2f chunks=%d",
                sym,
                total_notional,
                cap,
                len(order_qtys),
            )

        result["order_qtys"] = order_qtys
        return result

    def submit_exit_orders(
        self,
        symbol: str,
        qty: int,
        *,
        px: float,
        trading_client: Any = None,
        equity: float = 0.0,
        side: str = "SELL",
        max_chunk_size: float | None = None,
        inter_chunk_delay_sec: float = CHUNK_DELAY_MIN_SEC,
    ) -> dict[str, Any]:
        """
        Plan and submit exit chunks sequentially when notional exceeds FORTRESS_MAX_ORDER_NOTIONAL_USD.
        """
        plan = self.exit_position(
            symbol,
            qty,
            px=px,
            equity=equity,
            side=side,
            max_chunk_size=max_chunk_size,
        )
        if plan.get("block_reason"):
            return plan

        order_qtys = plan.get("order_qtys") or []
        if not order_qtys:
            plan["block_reason"] = "invalid_chunk_qty"
            return plan

        if trading_client is None:
            plan["detail"] = "dry_run_blocked"
            return plan

        sym = str(plan.get("symbol") or symbol or "").strip().upper()
        if str(side).upper() == "SELL":
            from utils.alpaca_execution import gate_exit_submission, set_open_exit_order_pending

            block = gate_exit_submission(sym, side=side)
            if block:
                plan["block_reason"] = block.get("block_reason")
                plan["detail"] = block.get("detail")
                return plan
            set_open_exit_order_pending(sym, True)

        try:
            from alpaca.trading.requests import MarketOrderRequest

            broker_side = "sell" if str(side).upper() == "SELL" else "buy"
            submitted: list[dict[str, Any]] = []
            for i, chunk_qty in enumerate(order_qtys):
                if i > 0 and len(order_qtys) > 1:
                    delay = max(float(inter_chunk_delay_sec or 0), CHUNK_DELAY_MIN_SEC)
                    log.info(
                        "chunked_exit:%s delay=%.3fs before chunk %d/%d",
                        sym,
                        delay,
                        i + 1,
                        len(order_qtys),
                    )
                    time.sleep(delay)
                order = trading_client.submit_order(
                    MarketOrderRequest(
                        symbol=sym,
                        qty=chunk_qty,
                        side=broker_side,
                        time_in_force="day",
                    )
                )
                submitted.append(
                    {"id": str(order.id), "qty": chunk_qty, "status": str(order.status)}
                )
            plan["submitted"] = submitted
            plan["max_notional_usd"] = plan.get("max_notional_usd") or float(FORTRESS_MAX_ORDER_NOTIONAL_USD)
            log.info(
                "chunked_exit submit_exit_orders %s orders=%d total_qty=%d",
                sym,
                len(submitted),
                sum(order_qtys),
            )
        except Exception as e:
            plan["error"] = f"{type(e).__name__}:{e}"
            log.warning("submit_exit_orders error %s: %s", symbol, e)
            if str(side).upper() == "SELL":
                from utils.alpaca_execution import set_open_exit_order_pending

                set_open_exit_order_pending(sym, False)
        else:
            if str(side).upper() == "SELL":
                from utils.alpaca_execution import on_exit_submit_result

                submitted = plan.get("submitted") or []
                if submitted:
                    last = submitted[-1]
                    on_exit_submit_result(
                        {
                            "success": True,
                            "order_id": last.get("id"),
                            "status": last.get("status"),
                        },
                        symbol=sym,
                    )

        return plan
