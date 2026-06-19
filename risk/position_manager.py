"""Risk-layer position management — entry checks and chunked exits under notional caps."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from utils.order_chunking import chunk_qtys, held_qty_for_symbol, max_order_notional_usd

log = logging.getLogger(__name__)


class PositionManager:
    """Lightweight view over broker position snapshots with chunked exit planning."""

    def __init__(
        self,
        positions: list[dict[str, Any]] | None = None,
        *,
        trading_client: Any = None,
    ) -> None:
        self._positions = list(positions or [])
        self._trading_client = trading_client

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        for p in self._positions:
            if not isinstance(p, dict):
                continue
            if str(p.get("sym") or p.get("symbol") or "").upper() != sym:
                continue
            try:
                qty = abs(float(p.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0:
                return p
        return None

    def has_open_position(self, symbol: str) -> bool:
        """Return True when symbol has a non-zero open quantity in the positions book."""
        return self.get_position(symbol) is not None

    def chunk_exit_orders(
        self,
        symbol: str,
        total_qty: int,
        max_notional: float | None = None,
        *,
        mark_price: float,
    ) -> list[int]:
        """
        Split a large exit into child order quantities, each <= max_notional.

        Uses FORTRESS_MAX_ORDER_NOTIONAL_USD when max_notional is omitted.
        """
        sym = (symbol or "").strip().upper()
        try:
            exit_qty = int(abs(float(total_qty or 0)))
        except (TypeError, ValueError):
            exit_qty = 0
        try:
            px = float(mark_price or 0)
        except (TypeError, ValueError):
            px = 0.0
        if not sym or exit_qty <= 0:
            return []

        cap = (
            float(max_notional)
            if max_notional is not None and float(max_notional) > 0
            else max_order_notional_usd(side="SELL", portfolio_equity_usd=None)
        )
        order_qtys = self._plan_exit_qtys(sym, exit_qty, px=px, cap=cap)
        if len(order_qtys) > 1:
            log.info(
                "chunked_exit chunk_exit_orders %s qty=%d px=%.2f cap=%.2f chunks=%d",
                sym,
                exit_qty,
                px,
                cap,
                len(order_qtys),
            )
        return order_qtys

    @staticmethod
    def _plan_exit_qtys(sym: str, exit_qty: int, *, px: float, cap: float) -> list[int]:
        if px > 0:
            order_qtys = chunk_qtys(exit_qty, px=px, max_notional_usd=cap)
        else:
            order_qtys = [exit_qty]
        return order_qtys

    def exit_position(
        self,
        symbol: str,
        qty: int,
        *,
        mark_price: float,
        max_notional: float | None = None,
        submit_one: Callable[[str, int], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Plan and optionally submit chunked sell orders under the notional cap.

        submit_one(symbol, chunk_qty) -> {success, order_id, filled_qty, filled_price, error}
        """
        sym = (symbol or "").strip().upper()
        try:
            exit_qty = int(abs(float(qty or 0)))
        except (TypeError, ValueError):
            exit_qty = 0
        try:
            px = float(mark_price or 0)
        except (TypeError, ValueError):
            px = 0.0

        result: dict[str, Any] = {
            "symbol": sym,
            "order_qtys": [],
            "chunked_exit": False,
        }
        if not sym or exit_qty <= 0:
            result["block_reason"] = "invalid_symbol_or_qty"
            return result

        cap = (
            float(max_notional)
            if max_notional is not None and float(max_notional) > 0
            else max_order_notional_usd(side="SELL", portfolio_equity_usd=None)
        )
        order_qtys = self.chunk_exit_orders(sym, exit_qty, cap, mark_price=px)
        if not order_qtys:
            result["block_reason"] = "invalid_chunk_qty"
            return result

        result["order_qtys"] = order_qtys
        result["max_notional_usd"] = cap
        if len(order_qtys) > 1:
            result["chunked_exit"] = True
            result["chunk_count"] = len(order_qtys)

        submit_fn = submit_one
        if submit_fn is None and self._trading_client is not None:
            submit_fn = self._default_submit_sell(self._trading_client)
        if submit_fn is None:
            return result

        submitted: list[dict[str, Any]] = []
        for i, chunk_qty in enumerate(order_qtys):
            if i > 0 and result.get("chunked_exit"):
                from utils.order_chunking import chunk_exit_delay_sec

                time.sleep(chunk_exit_delay_sec())
            res = submit_fn(sym, chunk_qty)
            submitted.append(res)
            if not res.get("success"):
                result["success"] = False
                result["submitted"] = submitted
                result["error"] = res.get("error")
                return result

        result["success"] = True
        result["submitted"] = submitted
        return result

    def flatten_oversized_legacy_positions(
        self,
        *,
        max_notional: float | None = None,
        submit_one: Callable[[str, int], dict[str, Any]] | None = None,
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """
        Scan all open positions; for any where abs(notional) > cap, plan chunked exits.

        Uses FORTRESS_MAX_ORDER_NOTIONAL_USD when max_notional is omitted.
        """
        cap = (
            float(max_notional)
            if max_notional is not None and float(max_notional) > 0
            else max_order_notional_usd(side="SELL", portfolio_equity_usd=equity if equity > 0 else None)
        )
        out: dict[str, Any] = {"flattened": [], "skipped": [], "max_notional_usd": cap}
        seen: set[str] = set()

        for p in self._positions:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("sym") or p.get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            try:
                qty = int(abs(float(p.get("qty") or 0)))
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            try:
                mkt = abs(float(p.get("mkt_value") or p.get("market_value") or 0))
            except (TypeError, ValueError):
                mkt = 0.0
            px = mkt / qty if qty > 0 else 0.0
            if mkt <= cap:
                out["skipped"].append({"symbol": sym, "notional_usd": mkt, "reason": "under_cap"})
                continue
            if px <= 0:
                out["skipped"].append({"symbol": sym, "reason": "no_price"})
                continue

            target_qty = max(1, int(cap // float(px)))
            if target_qty >= qty:
                out["skipped"].append({"symbol": sym, "notional_usd": mkt, "reason": "under_cap"})
                continue

            sell_qty = qty - target_qty
            order_qtys = self.chunk_exit_orders(sym, sell_qty, cap, mark_price=px)
            rec: dict[str, Any] = {
                "symbol": sym,
                "held_qty": qty,
                "sell_qty": sell_qty,
                "target_qty": target_qty,
                "notional_usd": mkt,
                "order_qtys": order_qtys,
                "chunked_exit": len(order_qtys) > 1,
                "chunk_count": len(order_qtys),
            }
            if order_qtys:
                log.info(
                    "chunked_exit flatten_oversized_legacy %s sell_qty=%d chunks=%d",
                    sym,
                    sell_qty,
                    len(order_qtys),
                )

            submit_fn = submit_one
            if submit_fn is None and self._trading_client is not None:
                submit_fn = self._default_submit_sell(self._trading_client)
            if submit_fn is not None and order_qtys:
                submitted: list[dict[str, Any]] = []
                for i, chunk_qty in enumerate(order_qtys):
                    if i > 0 and len(order_qtys) > 1:
                        from utils.order_chunking import chunk_exit_delay_sec

                        time.sleep(chunk_exit_delay_sec())
                    res = submit_fn(sym, chunk_qty)
                    submitted.append(res)
                    if not res.get("success"):
                        rec["submitted"] = submitted
                        rec["error"] = res.get("error")
                        out["flattened"].append(rec)
                        break
                else:
                    rec["submitted"] = submitted
                    out["flattened"].append(rec)
            else:
                out["flattened"].append(rec)

        return out

    def flatten_oversized_position(
        self,
        symbol: str,
        max_notional: float | None = None,
        *,
        px: float | None = None,
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """Alias for flatten_oversized_positions (singular symbol API)."""
        return self.flatten_oversized_positions(
            symbol,
            max_notional,
            px=px,
            equity=equity,
        )

    def flatten_oversized_positions(
        self,
        symbol: str | None = None,
        max_notional: float | None = None,
        *,
        px: float | None = None,
        equity: float = 0.0,
        submit_one: Callable[[str, int], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        When symbol is omitted, iterate all open positions and plan chunked exits for
        any whose notional exceeds FORTRESS_MAX_ORDER_NOTIONAL_USD.

        When symbol is provided, compare that position to cap and plan chunked exit
        orders for excess size.
        """
        if symbol is None:
            return self.flatten_oversized_legacy_positions(
                max_notional=max_notional,
                submit_one=submit_one,
                equity=equity,
            )

        sym = (symbol or "").strip().upper()
        pos = self.get_position(sym)
        if pos is None:
            return {"symbol": sym, "skipped": True, "reason": "no_position"}

        try:
            qty = int(abs(float(pos.get("qty") or 0)))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            return {"symbol": sym, "skipped": True, "reason": "no_position"}

        price = px
        if price is None or price <= 0:
            try:
                mkt = abs(float(pos.get("mkt_value") or pos.get("market_value") or 0))
            except (TypeError, ValueError):
                mkt = 0.0
            price = mkt / qty if qty > 0 else 0.0

        cap = (
            float(max_notional)
            if max_notional is not None and float(max_notional) > 0
            else max_order_notional_usd(side="SELL", portfolio_equity_usd=equity if equity > 0 else None)
        )
        notional = qty * float(price or 0)
        if notional <= cap:
            return {"symbol": sym, "skipped": True, "reason": "under_cap", "notional_usd": notional}
        if price <= 0:
            return {"symbol": sym, "skipped": True, "reason": "no_price"}

        held_qty = held_qty_for_symbol(self._positions, sym)
        sell_qty = qty
        if held_qty > 0:
            target_qty = max(1, int(cap // float(price)))
            if target_qty >= qty:
                return {"symbol": sym, "skipped": True, "reason": "under_cap", "notional_usd": notional}
            sell_qty = qty - target_qty

        plan = self.exit_position(sym, sell_qty, mark_price=float(price), max_notional=cap)
        plan["held_qty"] = qty
        plan["sell_qty"] = sell_qty
        plan["notional_usd"] = notional
        plan["max_notional_usd"] = cap
        return plan

    @staticmethod
    def _default_submit_sell(trading_client: Any) -> Callable[[str, int], dict[str, Any]]:
        def submit(sym: str, chunk_qty: int) -> dict[str, Any]:
            from alpaca.trading.requests import MarketOrderRequest

            order = trading_client.submit_order(
                MarketOrderRequest(
                    symbol=sym,
                    qty=chunk_qty,
                    side="sell",
                    time_in_force="day",
                )
            )
            return {
                "success": True,
                "order_id": str(order.id),
                "filled_qty": int(order.filled_qty) if order.filled_qty else chunk_qty,
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
                "error": None,
            }

        return submit
