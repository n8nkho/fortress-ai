"""Unified AI position lifecycle — entry deduplication and open-position queries."""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from unified_ai.settings import position_deduplication_enabled

log = logging.getLogger(__name__)


class PositionError(str, Enum):
    DUPLICATE_ENTRY = "duplicate_entry"
    INVALID_SYMBOL_OR_QTY = "invalid_symbol_or_qty"
    ENTER_COOLDOWN = "enter_cooldown"


class PositionDeduplicationError(Exception):
    """Raised when enter_position is blocked because symbol is already held."""

    def __init__(self, code: PositionError = PositionError.DUPLICATE_ENTRY, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code.value)


PositionExistsError = PositionDeduplicationError


class PositionManager:
    def __init__(
        self,
        positions: list[dict[str, Any]] | None = None,
        *,
        pending_entries: set[str] | None = None,
    ) -> None:
        self._positions = positions or []
        self._pending_entries = {str(s).strip().upper() for s in (pending_entries or set()) if s}

    @property
    def active_positions(self) -> dict[str, dict[str, Any]]:
        """Open positions keyed by symbol (qty > 0)."""
        out: dict[str, dict[str, Any]] = {}
        for p in self._positions:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("sym") or p.get("symbol") or "").strip().upper()
            if not sym or sym in out:
                continue
            try:
                qty = abs(float(p.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0:
                out[sym] = p
        return out

    def can_enter(self, symbol: str, *, held_qty: int | None = None) -> tuple[bool, str]:
        """Pre-trade gate: block duplicate entries and enter cooldown."""
        sym = (symbol or "").strip().upper()
        try:
            gate = self.enter_position(sym, 1, held_qty=held_qty)
        except PositionDeduplicationError as exc:
            return False, exc.detail or f"already_holding:{sym}"
        if gate is None or gate.get("allowed"):
            return True, ""
        reason = str(gate.get("detail") or gate.get("block_reason") or "blocked")
        return False, reason

    def has_position(self, symbol: str) -> bool:
        return self.has_open_position(symbol)

    def get_oversized_positions(self, max_notional: float) -> list[dict[str, Any]]:
        """Return open positions whose abs(notional) exceeds max_notional."""
        cap = float(max_notional or 0)
        if cap <= 0:
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for p in self._positions:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("sym") or p.get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            try:
                qty = abs(float(p.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                continue
            try:
                mkt = abs(float(p.get("mkt_value") or p.get("market_value") or 0))
            except (TypeError, ValueError):
                mkt = 0.0
            if mkt > cap:
                out.append({**p, "sym": sym, "notional_usd": mkt})
        return out

    def has_open_position(self, symbol: str) -> bool:
        return self.get_open_position(symbol) is not None

    def has_pending_entry(self, symbol: str) -> bool:
        """True when symbol has an in-flight buy (open order or recent enter guard)."""
        sym = (symbol or "").strip().upper()
        if not sym:
            return False
        if sym in self._pending_entries:
            return True
        if self.has_open_position(sym):
            return False
        try:
            from utils.unified_enter_guard import load_state
            from utils.system_time import parse_iso

            rec = (load_state().get("symbols") or {}).get(sym)
            if not isinstance(rec, dict):
                return False
            enter_ts = parse_iso(rec.get("last_enter_ts"))
            exit_ts = parse_iso(rec.get("last_exit_ts"))
            if enter_ts is None:
                return False
            if exit_ts is not None and exit_ts >= enter_ts:
                return False
            from datetime import datetime, timezone

            from utils.unified_enter_guard import enter_cooldown_sec

            age = (datetime.now(timezone.utc) - enter_ts).total_seconds()
            return age < enter_cooldown_sec()
        except Exception:
            return False

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Return open position dict for symbol, or None if flat."""
        return self.get_open_position(symbol)

    def _position_symbol(self, p: dict[str, Any]) -> str:
        return str(p.get("sym") or p.get("symbol") or "").strip().upper()

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return None
        for p in self._positions:
            if not isinstance(p, dict):
                continue
            if self._position_symbol(p) != sym:
                continue
            try:
                qty = abs(float(p.get("qty") or 0))
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0:
                return p
        return None

    def _check_symbol_held(self, symbol: str) -> bool:
        """Return True when symbol has an open position (local book or portfolio service)."""
        sym = (symbol or "").strip().upper()
        if not sym:
            return False
        if self.has_open_position(sym):
            return True
        try:
            from utils.alpaca_execution import trading_client
            from utils.order_chunking import held_qty_for_symbol

            tc = trading_client()
            if tc is None:
                return False
            broker_positions = [
                {
                    "sym": getattr(p, "symbol", ""),
                    "qty": float(getattr(p, "qty", 0) or 0),
                }
                for p in tc.get_all_positions()
            ]
            broker_qty = held_qty_for_symbol(broker_positions, sym)
            if broker_qty > 0:
                log.info("broker_held_qty:%s qty=%d", sym, broker_qty)
                return True
        except Exception:
            pass
        return False

    def _broker_held_qty(self, symbol: str) -> int:
        sym = (symbol or "").strip().upper()
        if not sym:
            return 0
        pos = self.get_open_position(sym)
        if pos is not None:
            try:
                return int(abs(float(pos.get("qty") or 0)))
            except (TypeError, ValueError):
                pass
        try:
            from utils.alpaca_execution import trading_client
            from utils.order_chunking import held_qty_for_symbol

            tc = trading_client()
            if tc is None:
                return 0
            broker_positions = [
                {
                    "sym": getattr(p, "symbol", ""),
                    "qty": float(getattr(p, "qty", 0) or 0),
                }
                for p in tc.get_all_positions()
            ]
            return held_qty_for_symbol(broker_positions, sym)
        except Exception:
            return 0

    def _reject(self, code: PositionError, *, sym: str, detail: str) -> dict[str, Any]:
        block_reason = {
            PositionError.DUPLICATE_ENTRY: "already_holding",
            PositionError.ENTER_COOLDOWN: "enter_cooldown",
            PositionError.INVALID_SYMBOL_OR_QTY: "invalid_symbol_or_qty",
        }.get(code, code.value)
        if code == PositionError.DUPLICATE_ENTRY:
            log.warning(
                "already_holding:%s entry_blocked_by_cooldown duplicate entry blocked (%s)",
                sym,
                detail,
            )
        elif code == PositionError.ENTER_COOLDOWN:
            log.warning("enter_cooldown entry_blocked_by_cooldown %s", detail)
        return {"allowed": False, "block_reason": block_reason, "detail": detail, "error": code.value}

    def enter_position(self, symbol: str, qty: int, *, held_qty: int | None = None) -> dict[str, Any] | None:
        """
        Gate duplicate entries. Returns allowed=True dict when ok.

        Raises PositionDeduplicationError when symbol is already in active_positions
        (before any order submission). Other blocks return a rejection dict.

        When POSITION_DEDUPLICATION_ENABLED is false, always returns allowed=True.
        """
        sym = (symbol or "").strip().upper()
        if not sym or int(qty or 0) <= 0:
            return self._reject(
                PositionError.INVALID_SYMBOL_OR_QTY,
                sym=sym,
                detail="invalid_symbol_or_qty",
            )

        if not position_deduplication_enabled():
            return {"allowed": True}

        if self.active_positions.get(sym) is not None:
            pos_qty = self._broker_held_qty(sym)
            if pos_qty <= 0 and held_qty is not None:
                pos_qty = int(held_qty)
            hq = held_qty if held_qty is not None else pos_qty
            qty_label = hq if hq > 0 else "pending"
            detail = f"already_holding:{sym}:{qty_label}"
            log.warning("Blocking duplicate entry for %s (active_positions)", sym)
            log.warning(
                "already_holding:%s entry_blocked_by_cooldown duplicate entry blocked (%s)",
                sym,
                detail,
            )
            raise PositionExistsError(
                PositionError.DUPLICATE_ENTRY,
                detail=detail or f"Symbol {sym} already held",
            )

        if self._check_symbol_held(sym):
            pos_qty = self._broker_held_qty(sym)
            if pos_qty <= 0 and held_qty is not None:
                pos_qty = int(held_qty)
            hq = held_qty if held_qty is not None else pos_qty
            qty_label = hq if hq > 0 else "pending"
            detail = f"already_holding:{sym}:{qty_label}"
            log.warning("Blocking duplicate entry for %s", sym)
            log.warning(
                "already_holding:%s entry_blocked_by_cooldown duplicate entry blocked (%s)",
                sym,
                detail,
            )
            raise PositionExistsError(
                PositionError.DUPLICATE_ENTRY,
                detail=detail or f"Symbol {sym} already held",
            )

        if self.has_pending_entry(sym):
            qty_label = "pending"
            return self._reject(
                PositionError.DUPLICATE_ENTRY,
                sym=sym,
                detail=f"already_holding:{sym}:{qty_label}",
            )

        from utils.unified_enter_guard import entry_blocked_by_cooldown

        hq = held_qty
        if hq is None:
            pos = self.get_open_position(sym)
            if pos is not None:
                try:
                    hq = int(abs(float(pos.get("qty") or 0)))
                except (TypeError, ValueError):
                    hq = 0

        if hq is not None and hq > 0:
            return self._reject(
                PositionError.DUPLICATE_ENTRY,
                sym=sym,
                detail=f"already_holding:{sym}:{hq}",
            )

        blocked, block_reason = entry_blocked_by_cooldown(sym, held_qty=hq or 0)
        if blocked:
            reason = block_reason or "enter_cooldown"
            br = reason.split(":")[0] if reason else "enter_cooldown"
            if br == "already_holding":
                return self._reject(PositionError.DUPLICATE_ENTRY, sym=sym, detail=reason)
            return self._reject(PositionError.ENTER_COOLDOWN, sym=sym, detail=reason)

        return {"allowed": True}

    def flatten_legacy_position(
        self,
        symbol: str,
        target_notional: float | None = None,
        *,
        px: float | None = None,
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """
        Plan chunked exit orders to trim symbol toward target_notional.

        Uses FORTRESS_MAX_ORDER_NOTIONAL_USD for per-order chunk sizing.
        """
        return self.flatten_oversized_positions(
            symbol,
            max_notional=target_notional,
            px=px,
            equity=equity,
        )

    def flatten_oversized_positions(
        self,
        symbol: str,
        max_notional: float | None = None,
        *,
        px: float | None = None,
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """
        Plan chunked exit orders when symbol notional exceeds max position cap.

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

        from unified_ai.exit_manager import ExitManager
        from unified_ai.settings import max_order_notional_usd, max_position_notional_usd

        position_cap = (
            float(max_notional)
            if max_notional is not None
            else max_position_notional_usd(portfolio_equity_usd=equity or None)
        )
        notional = qty * float(price or 0)
        if notional <= position_cap:
            return {"symbol": sym, "skipped": True, "reason": "under_cap", "notional_usd": notional}

        if price <= 0:
            return {"symbol": sym, "skipped": True, "reason": "no_price"}

        target_qty = max(1, int(position_cap // float(price)))
        if target_qty >= qty:
            return {"symbol": sym, "skipped": True, "reason": "under_cap", "notional_usd": notional}

        sell_qty = qty - target_qty
        order_cap = max_order_notional_usd(
            side="SELL",
            portfolio_equity_usd=equity if equity > 0 else None,
        )
        plan = ExitManager(self._positions, equity=equity).place_exit_order(
            sym,
            sell_qty,
            px=float(price),
            max_notional=order_cap,
            submit=False,
        )
        plan["held_qty"] = qty
        plan["sell_qty"] = sell_qty
        plan["target_qty"] = target_qty
        plan["notional_usd"] = notional
        plan["max_position_notional_usd"] = position_cap
        return plan

    @staticmethod
    def _chunk_order(symbol: str, total_qty: int, max_notional: float, *, px: float) -> list[int]:
        """Split total_qty into child order sizes each <= max_notional at px."""
        from unified_ai.order_utils import chunk_exit_orders

        return chunk_exit_orders(symbol, total_qty, max_notional, px=px)

    def exit_position(
        self,
        symbol: str,
        qty: int,
        *,
        px: float,
        equity: float = 0.0,
        max_chunk_size: float | None = None,
        side: str = "SELL",
    ) -> dict[str, Any]:
        """Plan exit orders, chunking when notional exceeds max_chunk_size."""
        from unified_ai.order_executor import OrderExecutor

        return OrderExecutor(self._positions).exit_position(
            symbol,
            qty,
            px=px,
            equity=equity,
            side=side,
            max_chunk_size=max_chunk_size,
        )

    def flatten_all_oversized_positions(
        self,
        max_notional: float | None = None,
        *,
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """Iterate open positions and plan chunked trims for any exceeding max position cap."""
        seen: set[str] = set()
        out: dict[str, Any] = {"flattened": [], "skipped": []}
        for p in self._positions:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("sym") or "").strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            plan = self.flatten_oversized_positions(
                sym,
                max_notional=max_notional,
                equity=equity,
            )
            if plan.get("skipped"):
                out["skipped"].append(plan)
            else:
                out["flattened"].append(plan)
        return out

    def flatten_legacy_positions(
        self,
        *,
        equity: float = 0.0,
        trading_client: Any = None,
        dry_run: bool | None = None,
        max_notional: float | None = None,
    ) -> dict[str, Any]:
        """
        Iterate open positions; chunk-exit excess when notional exceeds configurable cap.

        Default cap: min(position cap, 2× FORTRESS_MAX_ORDER_NOTIONAL_USD).
        """
        import os

        from unified_ai.exit_manager import ExitManager
        from unified_ai.settings import max_order_notional_usd, max_position_notional_usd

        order_cap = max_order_notional_usd(
            side="SELL",
            portfolio_equity_usd=equity if equity > 0 else None,
        )
        position_cap = max_position_notional_usd(
            portfolio_equity_usd=equity if equity > 0 else None,
        )
        threshold = (
            float(max_notional)
            if max_notional is not None
            else min(position_cap, 2.0 * order_cap)
        )
        if dry_run is None:
            dry_run = str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

        out: dict[str, Any] = {
            "flattened": [],
            "skipped": [],
            "dry_run": dry_run,
            "max_notional_usd": order_cap,
            "max_position_notional_usd": position_cap,
            "threshold_usd": threshold,
        }

        for pos in self.get_oversized_positions(threshold):
            sym = str(pos.get("sym") or "").strip().upper()
            try:
                qty = int(abs(float(pos.get("qty") or 0)))
            except (TypeError, ValueError):
                qty = 0
            try:
                mkt = abs(
                    float(
                        pos.get("mkt_value")
                        or pos.get("market_value")
                        or pos.get("notional_usd")
                        or 0
                    )
                )
            except (TypeError, ValueError):
                mkt = 0.0
            px = mkt / qty if qty > 0 else 0.0
            if qty <= 0 or px <= 0:
                out["skipped"].append({"symbol": sym, "reason": "no_price"})
                continue

            target_cap = min(position_cap, order_cap)
            target_qty = max(1, int(target_cap // px))
            if target_qty >= qty:
                out["skipped"].append({"symbol": sym, "notional_usd": mkt})
                continue

            sell_qty = qty - target_qty
            plan = ExitManager(
                self._positions,
                trading_client=trading_client,
                equity=equity,
                dry_run=dry_run,
            ).place_exit_order(
                sym,
                sell_qty,
                px=px,
                max_notional=order_cap,
                submit=not dry_run and trading_client is not None,
                equity=equity,
            )
            if plan.get("block_reason"):
                out["skipped"].append(
                    {"symbol": sym, "reason": plan.get("block_reason"), "detail": plan.get("detail")}
                )
                continue

            order_qtys = plan.get("order_qtys") or []
            rec: dict[str, Any] = {
                "symbol": sym,
                "held_qty": qty,
                "sell_qty": sell_qty,
                "target_qty": target_qty,
                "notional_usd": mkt,
                "chunked_exit": bool(plan.get("chunked_exit")),
                "chunk_count": plan.get("chunk_count") or len(order_qtys),
                "order_qtys": order_qtys,
            }
            if plan.get("detail"):
                rec["detail"] = plan["detail"]
            if plan.get("submitted"):
                rec["orders"] = plan["submitted"]
            if plan.get("error"):
                rec["error"] = plan["error"]
            out["flattened"].append(rec)
            log.info(
                "chunked_exit legacy_flatten %s sell_qty=%d chunks=%d",
                sym,
                sell_qty,
                len(order_qtys),
            )

        if out.get("flattened"):
            log.info(
                "chunked_exit flatten_legacy_positions flattened=%d",
                len(out.get("flattened") or []),
            )
        return out
