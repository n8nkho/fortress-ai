"""Price features and shared market context (no LLM)."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

from utils.skim_swarm_config import bar_cache_ttl_sec, bar_feed, bar_provider, normalize_symbol, thin_etf_symbols

_YF_TICKER: dict[str, str] = {
    "BRK.B": "BRK-B",
}

logger = logging.getLogger("skim_swarm.features")

_bar_cache: dict[str, tuple[float, pd.DataFrame]] = {}


def _cache_put(out: dict[str, pd.DataFrame], now: float) -> None:
    for sym, df in out.items():
        if df is not None and not df.empty:
            _bar_cache[sym] = (now, df)


def _fetch_bars_yfinance(symbols: list[str], *, now: float, out: dict[str, pd.DataFrame]) -> None:
    need = [s for s in symbols if s not in out]
    if not need:
        return
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
        logger.warning("yfinance bar download failed: %s", e)
        return
    if raw is None or raw.empty:
        return
    if len(need) == 1:
        sym = need[0]
        df = raw.copy()
        if not df.empty:
            out[sym] = df
            _bar_cache[sym] = (now, df)
        return
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


def _fetch_bars_alpaca(symbols: list[str], *, now: float, out: dict[str, pd.DataFrame]) -> None:
    need = [s for s in symbols if s not in out]
    if not need:
        return
    try:
        from agents.skim_swarm.alpaca_bars import fetch_intraday_bars
    except ImportError:
        return
    fetched = fetch_intraday_bars(need, feed=bar_feed())
    if not fetched:
        return
    for sym, df in fetched.items():
        out[sym] = df
    _cache_put(fetched, now)


def _use_alpaca_bars() -> bool:
    mode = bar_provider()
    if mode == "yfinance":
        return False
    if mode == "alpaca":
        return True
    try:
        from agents.skim_swarm.alpaca_bars import configured

        return configured()
    except ImportError:
        return False


def _fetch_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    import time

    now = time.time()
    ttl = bar_cache_ttl_sec()
    out: dict[str, pd.DataFrame] = {}
    need: list[str] = []
    for sym in symbols:
        hit = _bar_cache.get(sym)
        if hit and (now - hit[0]) < ttl:
            out[sym] = hit[1]
        else:
            need.append(sym)
    if not need:
        return out

    if _use_alpaca_bars():
        _fetch_bars_alpaca(need, now=now, out=out)
        missing = [s for s in need if s not in out]
        if missing:
            logger.info("alpaca bars missing %d symbols; yfinance fallback", len(missing))
            _fetch_bars_yfinance(missing, now=now, out=out)
    else:
        _fetch_bars_yfinance(need, now=now, out=out)
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
    spread_bps = None
    bar = bars.get(sym)
    if bar is not None and not bar.empty and last and last > 0:
        try:
            hi = float(bar["High"].astype(float).iloc[-1])
            lo = float(bar["Low"].astype(float).iloc[-1])
            spread_bps = max(0.0, (hi - lo) / float(last) * 10_000.0)
        except Exception:
            spread_bps = None
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
        "spy_r5m": spy_r5,
        "side": side,
        "qty": int(pos.get("qty") or 0),
        "unrealized_usd": unrealized,
        "unrealized_pct": unrealized_pct,
        "thin_etf": thin,
        "spread_bps": spread_bps,
    }
