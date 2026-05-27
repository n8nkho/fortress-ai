"""Company / ETF context for skim decisions (cached, no LLM)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.infra_swarm_config import normalize_symbol, swarm_data_dir

logger = logging.getLogger("infra_swarm.company_context")

_CONTEXT_TTL_HOURS = 24

_STATIC: dict[str, dict[str, Any]] = {
    "SPY": {
        "name": "SPDR S&P 500 ETF",
        "type": "etf",
        "sector": "Broad market",
        "summary": "US large-cap benchmark; use for regime and beta reference.",
        "peers": ["QQQ"],
    },
    "SOXX": {
        "name": "iShares Semiconductor ETF",
        "type": "etf",
        "sector": "Semiconductors",
        "summary": "Semi sector basket; leadership signal for NVDA/AVGO/MSFT.",
        "peers": ["NVDA", "AVGO"],
    },
    "NASA": {
        "name": "Tema Space Innovators ETF",
        "type": "etf",
        "sector": "Space / thematic",
        "summary": "Thematic space economy ETF; thinner liquidity, wider skim targets.",
        "peers": ["SPY"],
    },
    "AGIX": {
        "name": "KraneShares AI & Tech ETF",
        "type": "etf",
        "sector": "AI / tech thematic",
        "summary": "AI-themed ETF; correlate with mega-cap tech and SOXX.",
        "peers": ["NVDA", "MSFT", "GOOG"],
    },
    "AAPL": {
        "name": "Apple Inc",
        "type": "equity",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "summary": "Mega-cap consumer tech; iPhone, services, buybacks; tends to follow QQQ/SPY with lower beta.",
        "peers": ["MSFT", "GOOG", "AMZN"],
    },
    "NVDA": {
        "name": "NVIDIA Corporation",
        "type": "equity",
        "sector": "Technology",
        "industry": "Semiconductors",
        "summary": "AI/GPU leader; high intraday volatility; strong SOXX correlation.",
        "peers": ["SOXX", "AVGO", "MSFT"],
    },
    "MSFT": {
        "name": "Microsoft Corporation",
        "type": "equity",
        "sector": "Technology",
        "industry": "Software",
        "summary": "Cloud and enterprise software mega-cap; smoother trends than pure semi.",
        "peers": ["AAPL", "GOOG", "NVDA"],
    },
    "GOOG": {
        "name": "Alphabet Inc (Class C)",
        "type": "equity",
        "sector": "Communication Services",
        "industry": "Internet Content",
        "summary": "Search, cloud, YouTube; ad cycle sensitivity; mega-cap growth.",
        "peers": ["MSFT", "AMZN", "META"],
    },
    "AMZN": {
        "name": "Amazon.com Inc",
        "type": "equity",
        "sector": "Consumer Cyclical",
        "industry": "Internet Retail",
        "summary": "E-commerce and AWS; consumer + cloud mix; event-driven moves.",
        "peers": ["MSFT", "GOOG"],
    },
    "AVGO": {
        "name": "Broadcom Inc",
        "type": "equity",
        "sector": "Technology",
        "industry": "Semiconductors",
        "summary": "Networking and custom silicon; semi leader with dividend profile.",
        "peers": ["NVDA", "SOXX"],
    },
    "BRK.B": {
        "name": "Berkshire Hathaway",
        "type": "equity",
        "sector": "Financial Services",
        "industry": "Conglomerate",
        "summary": "Diversified holdings; lower beta, slower intraday skims.",
        "peers": ["SPY"],
    },
    "LLY": {
        "name": "Eli Lilly",
        "type": "equity",
        "sector": "Healthcare",
        "industry": "Drug Manufacturers",
        "summary": "GLP-1 / pharma leader; idiosyncratic news risk, less SOXX correlation.",
        "peers": ["XLV"],
    },
    "V": {
        "name": "Visa Inc",
        "type": "equity",
        "sector": "Financial Services",
        "industry": "Credit Services",
        "summary": "Payments network; macro and spend-trend sensitive.",
        "peers": ["MA", "SPY"],
    },
    "MA": {
        "name": "Mastercard Inc",
        "type": "equity",
        "sector": "Financial Services",
        "industry": "Credit Services",
        "summary": "Payments duopoly with V; similar intraday character to V.",
        "peers": ["V"],
    },
    "PLTR": {
        "name": "Palantir Technologies",
        "type": "equity",
        "sector": "Technology",
        "industry": "Software",
        "summary": "High-beta software; sentiment-driven, wider intraday ranges.",
        "peers": ["NVDA", "CRWD"],
    },
    "CRWD": {
        "name": "CrowdStrike Holdings",
        "type": "equity",
        "sector": "Technology",
        "industry": "Cybersecurity",
        "summary": "Cybersecurity growth; correlates with tech risk-on/off.",
        "peers": ["MSFT", "PLTR"],
    },
}


def _context_dir() -> Path:
    d = swarm_data_dir() / "company_context"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(sym: str) -> Path:
    return _context_dir() / f"{sym.replace('.', '_')}.json"


def _age_hours(ts: str | None) -> float:
    if not ts:
        return 999.0
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0
    except Exception:
        return 999.0


def _fetch_yfinance_info(sym: str) -> dict[str, Any]:
    try:
        import yfinance as yf

        t = yf.Ticker(sym.replace(".", "-") if sym == "BRK.B" else sym)
        info = t.info or {}
        summary = (info.get("longBusinessSummary") or info.get("description") or "")[:900]
        return {
            "name": info.get("shortName") or info.get("longName") or sym,
            "type": "etf" if str(info.get("quoteType") or "").lower() in ("etf", "mutualfund") else "equity",
            "sector": info.get("sector") or info.get("category"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "pe_ratio": info.get("trailingPE"),
            "dividend_yield": info.get("dividendYield"),
            "summary": summary or _STATIC.get(sym, {}).get("summary", ""),
            "fetched_from": "yfinance",
        }
    except Exception as e:
        logger.warning("yfinance info %s: %s", sym, e)
        return {}


def load_symbol_context(symbol: str, *, force_refresh: bool = False) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    base = dict(_STATIC.get(sym, {}))
    base.setdefault("symbol", sym)
    base.setdefault("summary", f"{sym} intraday skim symbol.")
    base.setdefault("type", "equity")

    p = _path(sym)
    if p.exists() and not force_refresh:
        try:
            cached = json.loads(p.read_text(encoding="utf-8"))
            if _age_hours(cached.get("updated_utc")) < _CONTEXT_TTL_HOURS:
                return {**base, **cached}
        except Exception:
            pass

    live = _fetch_yfinance_info(sym)
    doc = {
        **base,
        **live,
        "symbol": sym,
        "peers": base.get("peers") or [],
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except OSError:
        pass
    return doc


def load_all_contexts(symbols: list[str]) -> dict[str, dict[str, Any]]:
    return {s: load_symbol_context(s) for s in symbols}


def context_score_adjustment(ctx: dict[str, Any], features: dict[str, Any]) -> float:
    """Small score nudge from fundamentals + peers (deterministic)."""
    adj = 0.0
    beta = ctx.get("beta")
    if beta is not None:
        try:
            b = float(beta)
            if b > 1.3:
                adj += 0.03 * _f(features.get("r5m"))
            elif b < 0.9:
                adj -= 0.02 * abs(_f(features.get("r1m")))
        except (TypeError, ValueError):
            pass
    if ctx.get("type") == "etf":
        adj += 0.01 * _f(features.get("residual_vs_anchor"))
    layer = str(features.get("layer") or "")
    if layer in ("L1", "L2", "L3"):
        adj += 0.04 * _f(features.get("propagation_lag_vs_l1"))
    if layer == "L4":
        adj += 0.03 * _f(features.get("residual_vs_layer"))
    return max(-0.15, min(0.15, adj))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def format_context_blurb(ctx: dict[str, Any], max_len: int = 220) -> str:
    parts = [
        str(ctx.get("name") or ctx.get("symbol") or ""),
        str(ctx.get("sector") or ""),
        str(ctx.get("summary") or "")[:max_len],
    ]
    return " | ".join(p for p in parts if p).strip()[:max_len]
