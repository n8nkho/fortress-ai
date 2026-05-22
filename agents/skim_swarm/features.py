"""Price features and shared market context (no LLM)."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

from utils.skim_swarm_config import normalize_symbol, thin_etf_symbols

_YF_TICKER: dict[str, str] = {
    "BRK.B": "BRK-B",
}

logger = logging.getLogger("skim_swarm.features")

_bar_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_CACHE_TTL_SEC = 25.0


def _fetch_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    import time

    now = time.time()
    out: dict[str, pd.DataFrame] = {}
    need: list[str] = []
    for sym in symbols:
        hit = _bar_cache.get(sym)
        if hit and (now - hit[0]) < _CACHE_TTL_SEC:
            out[sym] = hit[1]
        else:
            need.append(sym)
    if not need:
        return out
    yf_need = [_YF_TICKER.get(s, s) for s in need]
    tickers = " ".join(yf_need)
    yf_to_sym = {yf: need[i] for i, yf in enumerate(yf_need)}
    try:
        raw = yf.download(
            tickers,
            period="1d",
            interval="1m",
            group_by="ticker",
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.warning("bar download failed: %s", e)
        return out
    if raw is None or raw.empty:
        return out
    if len(need) == 1:
        sym = need[0]
        df = raw.copy()
        if not df.empty:
            out[sym] = df
            _bar_cache[sym] = (now, df)
        return out
    for yf_sym, sym in yf_to_sym.items():
        try:
            if yf_sym not in raw.columns.get_level_values(0):
                continue
            df = raw[yf_sym].dropna(how="all")
            if not df.empty:
                out[sym] = df
                _bar_cache[sym] = (now, df)
        except Exception:
            continue
    return out


def _returns_from_df(df: pd.DataFrame) -> dict[str, float | None]:
    if df is None or df.empty or "Close" not in df.columns:
        return {"r1m": None, "r3m": None, "r5m": None, "atr1m": None, "rsi1m": None, "last": None}
    close = df["Close"].astype(float).dropna()
    if len(close) < 3:
        last = float(close.iloc[-1]) if len(close) else None
        return {"r1m": None, "r3m": None, "r5m": None, "atr1m": None, "rsi1m": None, "last": last}
    last = float(close.iloc[-1])
    r1 = (last / float(close.iloc[-2]) - 1.0) if len(close) >= 2 else None
    r3 = (last / float(close.iloc[-4]) - 1.0) if len(close) >= 4 else None
    r5 = (last / float(close.iloc[-6]) - 1.0) if len(close) >= 6 else None
    hl = (df["High"].astype(float) - df["Low"].astype(float)).tail(10)
    atr = float(hl.mean()) if len(hl) else None
    delta = close.diff().dropna()
    up = delta.clip(lower=0).tail(14).mean()
    down = (-delta.clip(upper=0)).tail(14).mean()
    if down and down > 0:
        rs = up / down if up else 0
        rsi = 100 - (100 / (1 + rs))
    else:
        rsi = 50.0
    return {"r1m": r1, "r3m": r3, "r5m": r5, "atr1m": atr, "rsi1m": float(rsi), "last": last}


def build_shared_context(bars: dict[str, pd.DataFrame]) -> dict[str, Any]:
    ctx: dict[str, Any] = {"symbols": {}}
    for sym, df in bars.items():
        ctx["symbols"][sym] = _returns_from_df(df)
    spy = ctx["symbols"].get("SPY") or {}
    soxx = ctx["symbols"].get("SOXX") or {}
    ctx["spy_r5m"] = spy.get("r5m")
    ctx["soxx_r5m"] = soxx.get("r5m")
    try:
        vix = yf.Ticker("^VIX")
        ctx["vix_last"] = float(vix.fast_info.get("last_price") or 0)
    except Exception:
        ctx["vix_last"] = None
    return ctx


def build_symbol_features(
    symbol: str,
    bars: dict[str, pd.DataFrame],
    shared: dict[str, Any],
    *,
    position: dict[str, Any] | None,
    company_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    local = _returns_from_df(bars.get(sym))
    spy_r5 = shared.get("spy_r5m")
    r5 = local.get("r5m")
    residual = None
    if r5 is not None and spy_r5 is not None:
        residual = r5 - spy_r5
    soxx_r5 = shared.get("soxx_r5m")
    semi_lead = None
    if sym in {"NVDA", "MSFT", "AVGO"} and soxx_r5 is not None and r5 is not None:
        semi_lead = r5 - soxx_r5
    pos = position or {}
    side = pos.get("side") or "flat"
    entry = pos.get("avg_entry_price") or pos.get("entry_price")
    last = local.get("last")
    unrealized = None
    unrealized_pct = None
    if last and entry and side in ("long", "short"):
        ep = float(entry)
        if side == "long":
            unrealized = last - ep
            unrealized_pct = unrealized / ep
        else:
            unrealized = ep - last
            unrealized_pct = unrealized / ep
    thin = sym in thin_etf_symbols()
    ctx = company_context if isinstance(company_context, dict) else {}
    return {
        "symbol": sym,
        "company_context": ctx,
        "company_name": ctx.get("name"),
        "company_sector": ctx.get("sector"),
        "company_beta": ctx.get("beta"),
        "company_summary": (ctx.get("summary") or "")[:200],
        "last": last,
        "r1m": local.get("r1m"),
        "r3m": local.get("r3m"),
        "r5m": r5,
        "atr1m": local.get("atr1m"),
        "rsi1m": local.get("rsi1m"),
        "residual_vs_spy": residual,
        "semi_lead_vs_soxx": semi_lead,
        "vix_last": shared.get("vix_last"),
        "side": side,
        "qty": int(pos.get("qty") or 0),
        "unrealized_usd": unrealized,
        "unrealized_pct": unrealized_pct,
        "thin_etf": thin,
    }
