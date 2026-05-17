"""Quant + qualitative context for index intraday decisions."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from utils.spy_agent_config import index_symbol

logger = logging.getLogger("spy_intraday.context")

# yfinance symbols: US index futures + global cash indices (context only — orders stay SPY/DIA ETF).
_FUTURES_TICKERS = (
    ("ES=F", "sp500_e_mini"),
    ("NQ=F", "nasdaq_e_mini"),
    ("YM=F", "dow_e_mini"),
    ("RTY=F", "russell_e_mini"),
)
_ASIA_TICKERS = (
    ("^N225", "nikkei"),
    ("^HSI", "hang_seng"),
    ("^KS11", "kospi"),
    ("^STI", "singapore"),
    ("000001.SS", "shanghai_composite"),
)
_EUROPE_TICKERS = (
    ("^GDAXI", "dax"),
    ("^FTSE", "ftse100"),
    ("^STOXX50E", "euro_stoxx50"),
    ("^FCHI", "cac40"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _long_term_spy_stats(sym: str) -> dict[str, Any]:
    out: dict[str, Any] = {"symbol": sym}
    try:
        t = yf.Ticker(sym)
        hist = t.history(period="max", interval="1d")
        if hist is None or len(hist) < 50:
            hist = t.history(period="20y", interval="1d")
        if hist is None or len(hist) < 20:
            out["error"] = "insufficient_history"
            return out
        close = hist["Close"].astype(float)
        last = float(close.iloc[-1])
        out["years_available"] = round(len(close) / 252, 1)
        out["last_close"] = round(last, 2)
        if len(close) >= 252:
            out["return_1y_pct"] = round((last / float(close.iloc[-252]) - 1) * 100, 2)
        if len(close) >= 252 * 5:
            out["return_5y_annualized_pct"] = round(
                ((last / float(close.iloc[-252 * 5])) ** (1 / 5) - 1) * 100, 2
            )
        if len(close) >= 252 * 10:
            out["return_10y_annualized_pct"] = round(
                ((last / float(close.iloc[-252 * 10])) ** (1 / 10) - 1) * 100, 2
            )
        ma50 = close.rolling(50).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1]
        out["above_ma50"] = bool(last > ma50)
        out["above_ma200"] = bool(last > ma200)
        out["ma50"] = round(float(ma50), 2)
        out["ma200"] = round(float(ma200), 2)
        ath = float(close.max())
        out["pct_from_ath"] = round((last / ath - 1) * 100, 2)
        # 20y trend: linear slope of log prices over last ~10y
        tail = close.iloc[-252 * 10 :]
        if len(tail) > 100:
            import numpy as np

            y = np.log(tail.values)
            x = np.arange(len(y))
            slope = np.polyfit(x, y, 1)[0] * 252
            out["log_trend_10y_annualized"] = round(float(slope) * 100, 2)
    except Exception as e:
        out["error"] = str(e)[:120]
    return out


def _intraday_structure(sym: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        t = yf.Ticker(sym)
        h5 = t.history(period="5d", interval="5m")
        if h5 is None or len(h5) < 10:
            return {"error": "no_5m_bars"}
        h5 = h5.tail(78)
        close = h5["Close"].astype(float)
        vol = h5["Volume"].astype(float)
        last = float(close.iloc[-1])
        open_today = float(close.iloc[0])
        out["last"] = round(last, 2)
        out["session_change_pct"] = round((last / open_today - 1) * 100, 3)
        out["bars_5m"] = len(close)
        vwap = float((close * vol).sum() / vol.sum()) if vol.sum() > 0 else last
        out["vwap"] = round(vwap, 2)
        out["vs_vwap_pct"] = round((last / vwap - 1) * 100, 3)
        # Morning vs afternoon split (first 1/3 vs last 1/3 of session bars)
        n = len(close)
        third = max(1, n // 3)
        am = float(close.iloc[:third].iloc[-1] / close.iloc[0] - 1) * 100
        pm = float(close.iloc[-1] / close.iloc[-third] - 1) * 100
        out["morning_leg_pct"] = round(am, 3)
        out["recent_leg_pct"] = round(pm, 3)
        if pm > 0.05 and am > 0:
            out["intraday_swell"] = "upward"
        elif pm < -0.05 and am < 0:
            out["intraday_swell"] = "downward"
        else:
            out["intraday_swell"] = "mixed"
    except Exception as e:
        out["error"] = str(e)[:120]
    return out


def _macro_vix() -> dict[str, Any]:
    try:
        vix = yf.Ticker("^VIX")
        spy = yf.Ticker("SPY")
        vx = float(vix.fast_info.get("last_price") or vix.history(period="5d")["Close"].iloc[-1])
        sp = float(spy.fast_info.get("last_price") or spy.history(period="5d")["Close"].iloc[-1])
        return {"vix": round(vx, 2), "spy": round(sp, 2)}
    except Exception as e:
        return {"error": str(e)[:80]}


def _global_markets_enabled() -> bool:
    return str(__import__("os").environ.get("FORTRESS_SPY_GLOBAL_MARKETS", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _day_change_pct(sym: str) -> dict[str, Any] | None:
    try:
        t = yf.Ticker(sym)
        h = t.history(period="10d", interval="1d")
        if h is None or len(h) < 2:
            return None
        c = h["Close"].astype(float)
        last = float(c.iloc[-1])
        prev = float(c.iloc[-2])
        chg = (last / prev - 1) * 100 if prev else 0.0
        return {"symbol": sym, "last": round(last, 2), "day_chg_pct": round(chg, 2)}
    except Exception as e:
        return {"symbol": sym, "error": str(e)[:60]}


def _futures_overnight_context() -> dict[str, Any]:
    """US equity index futures — leading indicator for RTH SPY open and intraday drift."""
    if not _global_markets_enabled():
        return {"enabled": False}
    rows: list[dict[str, Any]] = []
    for sym, label in _FUTURES_TICKERS:
        row = _day_change_pct(sym)
        if row:
            row["label"] = label
            rows.append(row)
    if not rows:
        return {"enabled": True, "contracts": [], "error": "no_futures_data"}
    avg = sum(r.get("day_chg_pct", 0) for r in rows if r.get("day_chg_pct") is not None) / max(len(rows), 1)
    tone = "risk_on" if avg > 0.15 else "risk_off" if avg < -0.15 else "neutral"
    # ES vs NQ spread: tech leadership
    es = next((r for r in rows if r.get("label") == "sp500_e_mini"), {})
    nq = next((r for r in rows if r.get("label") == "nasdaq_e_mini"), {})
    leadership = None
    if es.get("day_chg_pct") is not None and nq.get("day_chg_pct") is not None:
        leadership = "tech_leading" if nq["day_chg_pct"] > es["day_chg_pct"] + 0.1 else (
            "defensive_leading" if es["day_chg_pct"] > nq["day_chg_pct"] + 0.1 else "balanced"
        )
    return {
        "enabled": True,
        "contracts": rows,
        "avg_day_chg_pct": round(avg, 2),
        "tone": tone,
        "leadership": leadership,
        "note": "Context only; agent trades SPY/DIA ETF not futures contracts.",
    }


def _session_clock_et() -> dict[str, Any]:
    """Rough session labels in America/New_York (no holiday calendar)."""
    now = datetime.now(ZoneInfo("America/New_York"))
    wd = now.weekday()
    mins = now.hour * 60 + now.minute
    if wd >= 5:
        phase = "weekend"
    elif mins < 3 * 60:
        phase = "asia_late"
    elif mins < 9 * 60 + 30:
        phase = "europe_pre_us" if mins >= 3 * 60 else "asia_late"
    elif mins < 16 * 60:
        phase = "us_rth"
    else:
        phase = "us_after_hours"
    return {
        "now_et": now.isoformat(),
        "weekday": wd,
        "phase": phase,
        "description": {
            "weekend": "US closed; Asia/Europe may still trade — futures and overnight indices matter for Monday open.",
            "asia_late": "Asia session active; Europe not yet open — Nikkei/HSI moves lead US futures.",
            "europe_pre_us": "Europe open, US pre-market — DAX/FTSE often set tone before 9:30 ET.",
            "us_rth": "US regular hours — global cash markets mostly closed; futures still informative.",
            "us_after_hours": "US cash closed; Asia opening soon — watch futures for next-day bias.",
        }.get(phase, phase),
    }


def _region_snapshot(tickers: tuple[tuple[str, str], ...], region: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for sym, label in tickers:
        row = _day_change_pct(sym)
        if row:
            row["label"] = label
            rows.append(row)
    if not rows:
        return {"region": region, "indices": [], "avg_day_chg_pct": None, "tone": "unknown"}
    vals = [r["day_chg_pct"] for r in rows if r.get("day_chg_pct") is not None]
    avg = sum(vals) / len(vals) if vals else 0.0
    tone = "risk_on" if avg > 0.2 else "risk_off" if avg < -0.2 else "neutral"
    return {
        "region": region,
        "indices": rows,
        "avg_day_chg_pct": round(avg, 2),
        "tone": tone,
    }


def _global_sessions_context() -> dict[str, Any]:
    """Asia + Europe cash indices — overnight impact on US open and intraday bias."""
    if not _global_markets_enabled():
        return {"enabled": False}
    clock = _session_clock_et()
    asia = _region_snapshot(_ASIA_TICKERS, "asia")
    europe = _region_snapshot(_EUROPE_TICKERS, "europe")
    # Simple overnight read: if US is pre-RTH, weight Asia+Europe more in summary
    pre_us = clock.get("phase") in ("asia_late", "europe_pre_us", "weekend")
    combined_avg = None
    if asia.get("avg_day_chg_pct") is not None and europe.get("avg_day_chg_pct") is not None:
        combined_avg = round((asia["avg_day_chg_pct"] + europe["avg_day_chg_pct"]) / 2, 2)
    elif asia.get("avg_day_chg_pct") is not None:
        combined_avg = asia["avg_day_chg_pct"]
    elif europe.get("avg_day_chg_pct") is not None:
        combined_avg = europe["avg_day_chg_pct"]
    summary = "unknown"
    if combined_avg is not None:
        if combined_avg > 0.25:
            summary = "global_risk_on_overnight"
        elif combined_avg < -0.25:
            summary = "global_risk_off_overnight"
        else:
            summary = "global_mixed_overnight"
    return {
        "enabled": True,
        "session_clock_et": clock,
        "asia": asia,
        "europe": europe,
        "combined_overnight_avg_pct": combined_avg,
        "overnight_summary": summary,
        "weight_for_us_open": "high" if pre_us else "moderate",
    }


def _key_movers() -> list[dict[str, Any]]:
    tickers = ["SPY", "QQQ", "DIA", "IWM", "TLT", "GLD"]
    rows: list[dict[str, Any]] = []
    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            h = t.history(period="5d", interval="1d")
            if h is None or len(h) < 2:
                continue
            c = h["Close"].astype(float)
            chg = (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100
            rows.append({"symbol": sym, "day_chg_pct": round(chg, 2)})
        except Exception:
            continue
    rows.sort(key=lambda r: abs(r["day_chg_pct"]), reverse=True)
    return rows[:6]


def _regime_snapshot() -> dict[str, Any]:
    p = _repo_root() / "data" / "historical" / "features" / "SPY_features.csv"
    if not p.exists():
        return {"available": False}
    try:
        import pandas as pd

        df = pd.read_csv(p, parse_dates=["date"])
        if df.empty or "regime" not in df.columns:
            return {"available": False}
        last = df.iloc[-1]
        return {
            "available": True,
            "regime": str(last.get("regime")),
            "date": str(last.get("date")),
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:80]}


def _qualitative_snippet() -> dict[str, Any]:
    """News/macro ingest headlines (read-only context for LLM)."""
    try:
        from knowledge.domain_ingest_context import collect_valid_records

        recs = collect_valid_records(max_per_source=4)
        macro = [r for r in recs if r.get("signal_type") == "macro" or not r.get("ticker")][:4]
        spy_news = [r for r in recs if str(r.get("ticker", "")).upper() in ("SPY", "QQQ", "DIA")][:4]
        lines = []
        for r in macro + spy_news:
            title = (r.get("title") or r.get("headline") or r.get("summary") or "")[:120]
            src = r.get("source") or r.get("signal_type") or "ingest"
            if title:
                lines.append(f"[{src}] {title}")
        return {"headlines": lines[:8], "record_count": len(recs)}
    except Exception as e:
        return {"headlines": [], "error": str(e)[:80]}


def build_market_context() -> dict[str, Any]:
    sym = index_symbol()
    futures = _futures_overnight_context()
    global_sess = _global_sessions_context()
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "long_term": _long_term_spy_stats(sym),
        "intraday": _intraday_structure(sym),
        "macro": _macro_vix(),
        "futures": futures,
        "global_sessions": global_sess,
        "key_movers": _key_movers(),
        "regime_research": _regime_snapshot(),
        "qualitative": _qualitative_snippet(),
    }
