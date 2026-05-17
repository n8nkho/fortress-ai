"""Observation bundle: market context + Alpaca account + ladder state."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.spy_intraday.context import build_market_context
from agents.spy_intraday.eod import describe_eod_phase, session_date_et
from agents.spy_intraday.ladder import load_ladder_state, reset_session_if_new_day
from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs
from utils.spy_agent_config import index_symbol, instance_name, max_exposure_usd


def _alpaca_client():
    key, sec = alpaca_credentials()
    if not key or not sec:
        return None
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return None
    return TradingClient(key, sec, **alpaca_trading_client_kwargs())


def observe() -> dict[str, Any]:
    sym = index_symbol()
    ctx = build_market_context()
    ladder = reset_session_if_new_day(load_ladder_state(), session_date_et())

    out: dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "instance": instance_name(),
        "symbol": sym,
        "max_exposure_usd": max_exposure_usd(),
        "eod_phase": describe_eod_phase(),
        "market": ctx,
        "ladder": {
            "side": ladder.get("side"),
            "rungs_open": ladder.get("rungs_open"),
            "max_rungs": ladder.get("max_rungs"),
            "rung_notional_usd": ladder.get("rung_notional_usd"),
        },
        "alpaca_configured": False,
        "equity": None,
        "position": None,
    }

    tc = _alpaca_client()
    out["alpaca_configured"] = bool(tc)
    if tc:
        try:
            acct = tc.get_account()
            out["equity"] = float(acct.equity)
            positions = tc.get_all_positions()
            pos = None
            for p in positions:
                if str(getattr(p, "symbol", "")).upper() == sym:
                    qty = float(getattr(p, "qty", 0) or 0)
                    side = "long" if qty > 0 else "short" if qty < 0 else "flat"
                    mv = abs(float(getattr(p, "market_value", 0) or 0))
                    pos = {
                        "symbol": sym,
                        "qty": int(abs(qty)),
                        "side": side,
                        "market_value_usd": round(mv, 2),
                        "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
                    }
                    break
            out["position"] = pos or {"symbol": sym, "qty": 0, "side": "flat", "market_value_usd": 0.0}
            out["exposure_usd"] = float((pos or {}).get("market_value_usd") or 0)
        except Exception as e:
            out["alpaca_error"] = str(e)[:200]
    return out
