"""Unified AI order execution — chunked exits under notional caps."""
from __future__ import annotations

import logging
from typing import Any

from unified_ai.settings import max_order_notional_usd
from utils.order_chunking import chunk_qtys, held_qty_for_symbol

log = logging.getLogger(__name__)


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
        cap = max_order_notional_usd(side=side, portfolio_equity_usd=equity if equity > 0 else None)
        result["max_notional_usd"] = cap
        result["total_notional_usd"] = total_notional

        order_qtys = chunk_qtys(exit_qty, px=float(px), max_notional_usd=cap)
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
