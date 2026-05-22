"""Alpaca market-data bars for skim swarm (data API only — not the trading client)."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from utils.alpaca_env import alpaca_credentials
from utils.skim_swarm_config import normalize_symbol

logger = logging.getLogger("skim_swarm.alpaca_bars")

_client: Any = None
_client_lock = threading.Lock()
_fetch_lock = threading.Lock()

_COL_MAP = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def _data_client() -> Any | None:
    global _client
    key, sec = alpaca_credentials()
    if not key or not sec:
        return None
    with _client_lock:
        if _client is None:
            try:
                from alpaca.data.historical import StockHistoricalDataClient

                _client = StockHistoricalDataClient(key, sec)
            except ImportError:
                logger.warning("alpaca-py missing; cannot use Alpaca bars")
                return None
    return _client


def configured() -> bool:
    return _data_client() is not None


def _session_start_utc() -> datetime:
    et = ZoneInfo("America/New_York")
    now_et = datetime.now(et)
    start_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    return start_et.astimezone(timezone.utc)


def _resolve_feed(feed: str) -> Any:
    from alpaca.data.enums import DataFeed

    name = (feed or "iex").strip().lower()
    if name == "sip":
        return DataFeed.SIP
    return DataFeed.IEX


def _bars_end_utc(feed: str) -> datetime:
    """SIP on Basic requires end at least ~15 minutes in the past."""
    now = datetime.now(timezone.utc)
    if (feed or "iex").strip().lower() == "sip":
        from datetime import timedelta

        return now - timedelta(minutes=16)
    return now


def _normalize_symbol_df(raw: pd.DataFrame, sym: str) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    if isinstance(df.index, pd.MultiIndex):
        if sym in df.index.get_level_values(0):
            df = df.xs(sym, level=0)
        elif "symbol" in df.index.names:
            df = df.xs(sym, level="symbol")
        else:
            return None
    lower = {str(c).lower(): c for c in df.columns}
    rename = {lower[k]: _COL_MAP[k] for k in _COL_MAP if k in lower}
    if "close" not in lower and "Close" not in df.columns:
        return None
    out = df.rename(columns=rename)
    need = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in out.columns]
    if "Close" not in need:
        return None
    out = out[need].dropna(subset=["Close"])
    if out.empty:
        return None
    out.index = pd.to_datetime(out.index)
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    return out.sort_index()


def fetch_intraday_bars(symbols: list[str], *, feed: str = "iex") -> dict[str, pd.DataFrame]:
    """Fetch today's 1-minute bars for symbols in one batched data-API call."""
    need = [normalize_symbol(s) for s in symbols if normalize_symbol(s)]
    need = list(dict.fromkeys(need))
    if not need:
        return {}

    dc = _data_client()
    if dc is None:
        return {}

    with _fetch_lock:
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            req = StockBarsRequest(
                symbol_or_symbols=need,
                timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                start=_session_start_utc(),
                end=_bars_end_utc(feed),
                limit=10000,
                feed=_resolve_feed(feed),
            )
            resp = dc.get_stock_bars(req)
        except Exception as e:
            logger.warning("alpaca bar fetch failed: %s", e)
            return {}

    raw = resp.df
    if raw is None or raw.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}
    if isinstance(raw.index, pd.MultiIndex):
        for sym in need:
            try:
                if sym not in raw.index.get_level_values(0):
                    continue
                df = _normalize_symbol_df(raw.loc[sym], sym)
                if df is not None:
                    out[sym] = df
            except Exception:
                continue
    else:
        sym = need[0] if len(need) == 1 else None
        if sym:
            df = _normalize_symbol_df(raw, sym)
            if df is not None:
                out[sym] = df
    return out
