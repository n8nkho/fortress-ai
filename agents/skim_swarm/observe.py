"""Alpaca account snapshot for swarm."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs
from utils.skim_swarm_config import instance_name, normalize_symbol, universe


def _alpaca_client():
    key, sec = alpaca_credentials()
    if not key or not sec:
        return None
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return None
    return TradingClient(key, sec, **alpaca_trading_client_kwargs())


def fetch_positions_map() -> tuple[float | None, dict[str, dict[str, Any]]]:
    tc = _alpaca_client()
    if not tc:
        return None, {}
    equity = None
    out: dict[str, dict[str, Any]] = {}
    try:
        acct = tc.get_account()
        equity = float(acct.equity)
        uni = set(universe())
        for p in tc.get_all_positions():
            raw = str(getattr(p, "symbol", "")).upper()
            sym = normalize_symbol(raw)
            if sym not in uni and raw not in uni:
                continue
            qty = float(getattr(p, "qty", 0) or 0)
            if qty == 0:
                continue
            side = "long" if qty > 0 else "short"
            out[sym] = {
                "symbol": sym,
                "qty": int(abs(qty)),
                "side": side,
                "avg_entry_price": float(getattr(p, "avg_entry_price", 0) or 0),
                "current_price": float(getattr(p, "current_price", 0) or 0) or None,
                "market_value_usd": abs(float(getattr(p, "market_value", 0) or 0)),
                "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
                "unrealized_plpc": float(getattr(p, "unrealized_plpc", 0) or 0),
            }
    except Exception:
        pass
    for sym in universe():
        if sym not in out:
            out[sym] = {"symbol": sym, "qty": 0, "side": "flat", "avg_entry_price": None, "market_value_usd": 0.0}
    return equity, out


def observe_account() -> dict[str, Any]:
    equity, positions = fetch_positions_map()
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "instance": instance_name(),
        "equity": equity,
        "positions": positions,
        "universe": universe(),
        "alpaca_configured": equity is not None,
    }
