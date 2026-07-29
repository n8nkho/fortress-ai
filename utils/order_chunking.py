"""Shared order sizing helpers — chunk large exits under notional caps."""
from __future__ import annotations

import os
import random
from typing import Any

CHUNK_DELAY_MIN_SEC = 0.1
CHUNK_DELAY_MAX_SEC = 0.5


def held_qty_for_symbol(positions: list[Any] | None, symbol: str) -> int:
    sym = (symbol or "").strip().upper()
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("sym") or "").upper() != sym:
            continue
        try:
            return int(abs(float(p.get("qty") or 0)))
        except (TypeError, ValueError):
            return 0
    return 0


def max_order_notional_usd(*, side: str, portfolio_equity_usd: float | None) -> float:
    env_raw = (os.environ.get("FORTRESS_MAX_ORDER_NOTIONAL_USD") or "").strip()
    if env_raw:
        try:
            max_notional = float(env_raw)
        except ValueError:
            max_notional = 25000.0
    else:
        try:
            from config.risk_params import FORTRESS_MAX_ORDER_NOTIONAL_USD as _cfg_cap

            max_notional = float(_cfg_cap)
        except Exception:
            try:
                from config.defaults import FORTRESS_MAX_ORDER_NOTIONAL_USD as _cfg_cap

                max_notional = float(_cfg_cap)
            except Exception:
                max_notional = 25000.0
    sd = (side or "").strip().upper()
    if sd == "BUY" and portfolio_equity_usd is not None and float(portfolio_equity_usd) > 0:
        try:
            from utils.tunable_overrides import get_position_size_pct

            position_pct_cap = float(portfolio_equity_usd) * float(get_position_size_pct())
            if position_pct_cap > 0:
                max_notional = min(max_notional, position_pct_cap)
        except Exception:
            pass
    return max_notional


def chunk_qtys(total_qty: int, *, px: float, max_notional_usd: float) -> list[int]:
    """Split total_qty into order chunks that each fit under max_notional_usd."""
    if total_qty <= 0:
        return []
    if px <= 0:
        return [total_qty]
    max_per = max(1, int(max_notional_usd // float(px)))
    chunks: list[int] = []
    remaining = int(total_qty)
    while remaining > 0:
        q = min(remaining, max_per)
        chunks.append(q)
        remaining -= q
    return chunks


def chunk_exit_delay_sec() -> float:
    """Random inter-order delay for chunked exits."""
    return random.uniform(CHUNK_DELAY_MIN_SEC, CHUNK_DELAY_MAX_SEC)


def chunk_exit_order(
    symbol: str,
    total_qty: int,
    max_notional: float | None = None,
    *,
    px: float,
) -> list[tuple[int, float]]:
    """
    Split total_qty into (qty, price) tuples where each chunk notional <= max_notional.

    Uses FORTRESS_MAX_ORDER_NOTIONAL_USD when max_notional is omitted.
    """
    cap = float(max_notional) if max_notional is not None else max_order_notional_usd(
        side="SELL", portfolio_equity_usd=None
    )
    price = float(px or 0)
    qtys = chunk_qtys(int(total_qty), px=price, max_notional_usd=cap)
    if not qtys:
        return []
    return [(q, price) for q in qtys]
