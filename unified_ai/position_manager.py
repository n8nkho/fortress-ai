"""Unified AI position lifecycle — entry deduplication and open-position queries."""
from __future__ import annotations

import logging
from typing import Any

from unified_ai.settings import position_deduplication_enabled

log = logging.getLogger(__name__)


class PositionDeduplicationError(Exception):
    """Raised when enter_position is blocked because symbol is already held."""


class PositionManager:
    def __init__(self, positions: list[dict[str, Any]] | None = None) -> None:
        self._positions = positions or []

    def has_position(self, symbol: str) -> bool:
        return self.get_open_position(symbol) is not None

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        for p in self._positions:
            if not isinstance(p, dict):
                continue
            if str(p.get("sym") or "").upper() != sym:
                continue
            try:
                qty = abs(float(p.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0:
                return p
        return None

    def enter_position(self, symbol: str, qty: int, *, held_qty: int | None = None) -> dict[str, Any] | None:
        """
        Gate duplicate entries. Returns None when blocked; dict with allowed=True when ok.

        When POSITION_DEDUPLICATION_ENABLED is false, always returns allowed=True.
        """
        sym = (symbol or "").strip().upper()
        if not sym or int(qty or 0) <= 0:
            return {"allowed": False, "block_reason": "invalid_symbol_or_qty", "detail": "invalid_symbol_or_qty"}

        from utils.unified_enter_guard import entry_blocked_by_cooldown

        hq = held_qty
        if hq is None:
            pos = self.get_open_position(sym)
            if pos is not None:
                try:
                    hq = int(abs(float(pos.get("qty") or 0)))
                except (TypeError, ValueError):
                    hq = 0

        if position_deduplication_enabled() and self.has_position(sym):
            pos = self.get_open_position(sym)
            if pos is not None:
                try:
                    pos_qty = int(abs(float(pos.get("qty") or 0)))
                except (TypeError, ValueError):
                    pos_qty = 0
                held = hq if (hq is not None and hq > 0) else pos_qty
                log.warning(
                    "already_holding:%s:%s entry_blocked_by_cooldown duplicate entry blocked",
                    sym,
                    held,
                )
                return {
                    "allowed": False,
                    "block_reason": "already_holding",
                    "detail": f"already_holding:{sym}:{held}",
                }
            if hq is not None and hq > 0:
                log.warning(
                    "already_holding:%s:%s entry_blocked_by_cooldown duplicate entry blocked",
                    sym,
                    hq,
                )
                return {
                    "allowed": False,
                    "block_reason": "already_holding",
                    "detail": f"already_holding:{sym}:{hq}",
                }

        blocked, block_reason = entry_blocked_by_cooldown(sym, held_qty=hq or 0)
        if blocked:
            reason = block_reason or "enter_cooldown"
            br = reason.split(":")[0] if reason else "enter_cooldown"
            log.warning("enter_cooldown entry_blocked_by_cooldown %s", reason)
            return {"allowed": False, "block_reason": br, "detail": reason}

        return {"allowed": True}

    def flatten_oversized_positions(
        self,
        symbol: str,
        max_notional: float | None = None,
        *,
        px: float | None = None,
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """
        Plan chunked exit orders when symbol notional exceeds max_notional.

        Returns exit plan from OrderExecutor or skip metadata when under cap.
        """
        sym = (symbol or "").strip().upper()
        pos = self.get_open_position(sym)
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

        from unified_ai.settings import max_order_notional_usd
        from unified_ai.order_executor import OrderExecutor

        cap = float(max_notional) if max_notional is not None else max_order_notional_usd(side="SELL", portfolio_equity_usd=equity or None)
        notional = qty * float(price or 0)
        if notional <= cap:
            return {"symbol": sym, "skipped": True, "reason": "under_cap", "notional_usd": notional}

        if price <= 0:
            return {"symbol": sym, "skipped": True, "reason": "no_price"}

        target_qty = max(1, int(cap // float(price)))
        if target_qty >= qty:
            return {"symbol": sym, "skipped": True, "reason": "under_cap", "notional_usd": notional}

        sell_qty = qty - target_qty
        plan = OrderExecutor(self._positions).exit_position(
            sym,
            sell_qty,
            px=float(price),
            equity=equity,
            side="SELL",
        )
        plan["held_qty"] = qty
        plan["sell_qty"] = sell_qty
        plan["target_qty"] = target_qty
        plan["notional_usd"] = notional
        return plan
