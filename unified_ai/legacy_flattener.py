"""Flatten legacy oversized positions that exceed order notional caps."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from unified_ai.config import FORTRESS_MAX_ORDER_NOTIONAL_USD
from unified_ai.settings import max_order_notional_usd, max_position_notional_usd
from utils.order_chunking import CHUNK_DELAY_MIN_SEC, chunk_qtys

log = logging.getLogger(__name__)


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


def flatten_oversized_positions(
    trading_client: Any,
    positions: list[Any] | None,
    *,
    dry_run: bool | None = None,
    equity: float | None = None,
) -> dict[str, Any]:
    """
    Scan open positions; for any whose notional exceeds FORTRESS_MAX_ORDER_NOTIONAL_USD,
    sell excess in chunked market orders to reduce toward the cap.

    Returns summary with symbols trimmed and orders submitted (or dry_run detail).
    """
    if dry_run is None:
        dry_run = str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).strip().lower() in ("1", "true", "yes", "on")

    cap = max_order_notional_usd(side="SELL", portfolio_equity_usd=equity)
    position_cap = max_position_notional_usd(portfolio_equity_usd=equity)
    order_cap = min(float(FORTRESS_MAX_ORDER_NOTIONAL_USD), cap)
    threshold = order_cap
    out: dict[str, Any] = {
        "flattened": [],
        "skipped": [],
        "dry_run": dry_run,
        "max_notional_usd": order_cap,
        "max_position_notional_usd": position_cap,
        "threshold_usd": threshold,
    }
    if not positions:
        return out

    for p in positions:
        sym, qty, mkt, px = _position_fields(p)
        if not sym or qty <= 0 or mkt <= threshold:
            if sym:
                out["skipped"].append({"symbol": sym, "notional_usd": mkt})
            continue

        if px <= 0:
            out["skipped"].append({"symbol": sym, "reason": "no_price"})
            continue

        target_cap = min(position_cap, order_cap)
        target_qty = max(1, int(target_cap // px))
        if target_qty >= qty:
            out["skipped"].append({"symbol": sym, "notional_usd": mkt})
            continue

        sell_qty = qty - target_qty
        chunks = chunk_qtys(sell_qty, px=px, max_notional_usd=order_cap)
        rec: dict[str, Any] = {
            "symbol": sym,
            "held_qty": qty,
            "sell_qty": sell_qty,
            "target_qty": target_qty,
            "notional_usd": mkt,
            "chunked_exit": len(chunks) > 1,
            "chunk_count": len(chunks),
        }

        if dry_run or trading_client is None:
            rec["detail"] = "dry_run_blocked"
            out["flattened"].append(rec)
            log.info(
                "chunked_exit legacy_flatten dry_run %s sell_qty=%d chunks=%d",
                sym,
                sell_qty,
                len(chunks),
            )
            continue

        try:
            from alpaca.trading.requests import MarketOrderRequest

            submitted: list[dict[str, Any]] = []
            for i, chunk_qty in enumerate(chunks):
                if i > 0 and len(chunks) > 1:
                    delay = CHUNK_DELAY_MIN_SEC
                    log.info(
                        "chunked_exit:%s delay=%.3fs before chunk %d/%d",
                        sym,
                        delay,
                        i + 1,
                        len(chunks),
                    )
                    time.sleep(delay)
                order = trading_client.submit_order(
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


class LegacyFlattener:
    """Stateful wrapper for startup and per-cycle legacy position flattening."""

    def __init__(
        self,
        trading_client: Any = None,
        *,
        equity: float | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self._trading_client = trading_client
        self._equity = equity
        self._dry_run = dry_run

    def flatten_oversized_positions(
        self,
        positions: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Iterate open positions and chunk-exit any exceeding FORTRESS_MAX_ORDER_NOTIONAL_USD."""
        return flatten_oversized_positions(
            self._trading_client,
            positions,
            dry_run=self._dry_run,
            equity=self._equity,
        )
