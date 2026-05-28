"""Layer-aware price features for AI infra stack propagation."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

from utils.infra_swarm_config import (
    anchor_symbol,
    bar_cache_ttl_sec,
    bar_feed,
    bar_provider,
    candidate_pool,
    layer_for_symbol,
    layer_symbols,
    normalize_symbol,
)
from utils.movement_anticipation import enrich_features_with_anticipation

logger = logging.getLogger("infra_swarm.features")

_YF_TICKER: dict[str, str] = {"BRK.B": "BRK-B"}
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
        raw = yf.download(tickers, period="1d", interval="1m", group_by="ticker", progress=False, threads=True)
    except Exception as e:
        logger.warning("yfinance bar download failed: %s", e)
        return
    if raw is None or raw.empty:
        return
    if len(need) == 1:
        sym = need[0]
        if not raw.empty:
            out[sym] = raw.copy()
            _bar_cache[sym] = (now, raw.copy())
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


def _layer_basket_r5(layer: str, ctx: dict[str, Any]) -> float | None:
    rets = []
    for sym in layer_symbols(layer):
        r5 = (ctx.get("symbols") or {}).get(sym, {}).get("r5m")
        if r5 is not None:
            rets.append(float(r5))
    if not rets:
        return None
    return sum(rets) / len(rets)


def build_shared_context(bars: dict[str, pd.DataFrame]) -> dict[str, Any]:
    ctx: dict[str, Any] = {"symbols": {}}
    for sym, df in bars.items():
        ctx["symbols"][sym] = _returns_from_df(df)
    anchor = anchor_symbol()
    anchor_r5 = (ctx["symbols"].get(anchor) or {}).get("r5m")
    ctx["anchor_r5m"] = anchor_r5
    ctx["anchor_symbol"] = anchor
    ctx["l1_r5m"] = _layer_basket_r5("L1", ctx)
    ctx["l2_r5m"] = _layer_basket_r5("L2", ctx)
    ctx["l3_r5m"] = _layer_basket_r5("L3", ctx)
    ctx["l4_r5m"] = _layer_basket_r5("L4", ctx)
    try:
        vix = yf.Ticker("^VIX")
        ctx["vix_last"] = float(vix.fast_info.get("last_price") or 0)
    except Exception:
        ctx["vix_last"] = None
    layers = [ctx.get(f"l{i}_r5m") for i in range(1, 5)]
    aligned = [x for x in layers if x is not None]
    if len(aligned) >= 3:
        pos = sum(1 for x in aligned if x > 0.0003)
        neg = sum(1 for x in aligned if x < -0.0003)
        ctx["stack_stress"] = max(pos, neg)
        ctx["stack_direction"] = 1 if pos > neg else (-1 if neg > pos else 0)
    else:
        ctx["stack_stress"] = 0
        ctx["stack_direction"] = 0
    active = [s for s in candidate_pool() if s in ctx["symbols"]]
    pos_n = sum(1 for s in active if (ctx["symbols"][s].get("r5m") or 0) > 0)
    ctx["infra_breadth"] = pos_n / max(len(active), 1)
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
    layer = layer_for_symbol(sym)
    layer_key = f"{layer.lower()}_r5m"
    layer_r5 = shared.get(layer_key)
    r5 = local.get("r5m")
    residual_layer = None
    if r5 is not None and layer_r5 is not None:
        residual_layer = r5 - layer_r5
    l1_r5 = shared.get("l1_r5m")
    lead_impulse = l1_r5
    propagation_lag = None
    if l1_r5 is not None and r5 is not None and layer != "L1":
        propagation_lag = l1_r5 - r5
    anchor_r5 = shared.get("anchor_r5m")
    residual_anchor = None
    if r5 is not None and anchor_r5 is not None:
        residual_anchor = r5 - anchor_r5
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
        "layer": layer,
        "company_context": ctx,
        "company_name": ctx.get("name"),
        "company_sector": ctx.get("sector"),
        "company_beta": ctx.get("beta"),
        "last": last,
        "r1m": local.get("r1m"),
        "r3m": local.get("r3m"),
        "r5m": r5,
        "atr1m": local.get("atr1m"),
        "rsi1m": local.get("rsi1m"),
        "residual_vs_layer": residual_layer,
        "residual_vs_anchor": residual_anchor,
        "lead_impulse_l1": lead_impulse,
        "propagation_lag_vs_l1": propagation_lag,
        "layer_r5m": layer_r5,
        "stack_stress": shared.get("stack_stress"),
        "stack_direction": shared.get("stack_direction"),
        "infra_breadth": shared.get("infra_breadth"),
        "vix_last": shared.get("vix_last"),
        "side": side,
        "qty": int(pos.get("qty") or 0),
        "unrealized_usd": unrealized,
        "unrealized_pct": unrealized_pct,
        "spread_bps": spread_bps,
    }
    out["anchor_r5m"] = shared.get("anchor_r5m")
    enrich_features_with_anticipation(out, component="infra_swarm")
    return out
