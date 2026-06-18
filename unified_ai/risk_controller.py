"""Unified AI risk controls — flatten legacy oversized positions."""
from __future__ import annotations

import logging
import os
from typing import Any

from unified_ai.order_executor import OrderExecutor
from unified_ai.settings import max_order_notional_usd

log = logging.getLogger(__name__)

FLATTEN_INTERVAL_SEC = 300.0


def _position_fields(p: Any) -> tuple[str, int, float, float]:
    if isinstance(p, dict):
        sym = str(p.get("sym") or p.get("symbol") or "").upper()
        try:
            qty = int(abs(float(p.get("qty") or 0)))
        except (TypeError, ValueError):
            qty = 0
        try:
            mkt = abs(float(p.get("mkt_value") or p.get("market_value") or 0))
        except (TypeError, ValueError):
            mkt = 0.0
        px = mkt / qty if qty > 0 else 0.0
        return sym, qty, mkt, px
    sym = str(getattr(p, "symbol", "") or "").upper()
    try:
        qty = int(abs(float(getattr(p, "qty", 0) or 0)))
    except (TypeError, ValueError):
        qty = 0
    try:
        mkt = abs(float(getattr(p, "market_value", 0) or 0))
    except (TypeError, ValueError):
        mkt = 0.0
    px = mkt / qty if qty > 0 else 0.0
    return sym, qty, mkt, px


class RiskController:
    def __init__(
        self,
        positions: list[Any] | None = None,
        *,
        trading_client: Any = None,
        equity: float | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self._positions = list(positions or [])
        self._trading_client = trading_client
        self._equity = equity
        if dry_run is None:
            dry_run = str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        self._dry_run = dry_run

    def flatten_legacy_positions(self) -> dict[str, Any]:
        """
        Trim positions whose notional exceeds the cap by selling excess in chunked exits.
        """
        cap = max_order_notional_usd(side="SELL", portfolio_equity_usd=self._equity)
        out: dict[str, Any] = {
            "flattened": [],
            "skipped": [],
            "dry_run": self._dry_run,
            "max_notional_usd": cap,
        }
        if not self._positions:
            return out

        executor = OrderExecutor(self._positions)
        for p in self._positions:
            sym, qty, mkt, px = _position_fields(p)
            if not sym or qty <= 0 or mkt <= cap:
                if sym:
                    out["skipped"].append({"symbol": sym, "notional_usd": mkt})
                continue

            if px <= 0:
                out["skipped"].append({"symbol": sym, "reason": "no_price"})
                continue

            target_qty = max(1, int(cap // px))
            if target_qty >= qty:
                out["skipped"].append({"symbol": sym, "notional_usd": mkt})
                continue

            sell_qty = qty - target_qty
            plan = executor.exit_position(
                sym,
                sell_qty,
                px=px,
                equity=float(self._equity or 0),
                side="SELL",
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

            if self._dry_run or self._trading_client is None:
                rec["detail"] = "dry_run_blocked"
                out["flattened"].append(rec)
                log.info(
                    "chunked_exit legacy_flatten dry_run %s sell_qty=%d chunks=%d",
                    sym,
                    sell_qty,
                    len(order_qtys),
                )
                continue

            try:
                from alpaca.trading.requests import MarketOrderRequest

                submitted: list[dict[str, Any]] = []
                for chunk_qty in order_qtys:
                    order = self._trading_client.submit_order(
                        MarketOrderRequest(
                            symbol=sym,
                            qty=chunk_qty,
                            side="sell",
                            time_in_force="day",
                        )
                    )
                    submitted.append({"id": str(order.id), "qty": chunk_qty, "status": str(order.status)})
                rec["orders"] = submitted
                out["flattened"].append(rec)
                log.info(
                    "chunked_exit legacy_flatten %s sell_qty=%d orders=%d",
                    sym,
                    sell_qty,
                    len(submitted),
                )
            except Exception as e:
                rec["error"] = f"{type(e).__name__}:{e}"
                out["flattened"].append(rec)
                log.warning("legacy_flatten error %s: %s", sym, e)

        return out
