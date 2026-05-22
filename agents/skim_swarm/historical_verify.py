"""Historical strategy verification per symbol (daily-bar proxy for skim patterns).

Note: Live skim uses 1-minute bars; Yahoo daily history supports 10-20+ years for
most names. This replays the same entry/exit *logic* on daily returns as a
calibration proxy — not a tick-perfect P&L forecast.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.skim_swarm_config import (
    atr_k,
    max_stop_usd,
    min_target_pct,
    min_target_usd,
    semi_symbols,
    stop_target_mult,
    thin_etf_symbols,
    universe,
)

_PATTERNS = ("rip_fade", "pullback_uptrend", "momentum_long", "momentum_short")
_YAHOO = {"BRK.B": "BRK-B"}


@dataclass
class SimConfig:
    years: int = 10
    train_frac: float = 0.7
    enter_long_base: float = 0.22
    enter_short_base: float = -0.22
    thin_enter_long: float = 0.24
    thin_enter_short: float = -0.24
    max_hold_bars: int = 5


@dataclass
class PatternStats:
    trades: int = 0
    wins: int = 0
    sum_pnl_usd: float = 0.0
    stop_loss: int = 0
    target_hit: int = 0


def _yahoo(sym: str) -> str:
    return _YAHOO.get(sym.upper(), sym)


def fetch_daily(symbol: str, years: int) -> pd.DataFrame:
    import yfinance as yf

    start = f"{datetime.now().year - years}-01-01"
    raw = yf.download(
        _yahoo(symbol),
        start=start,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [str(c[0]).lower() for c in raw.columns]
    else:
        raw.columns = [str(c).lower() for c in raw.columns]
    df = raw.rename(columns={"close": "close", "high": "high", "low": "low"}).copy()
    df = df.reset_index()
    date_col = "date" if "date" in df.columns else df.columns[0]
    df["date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            return pd.DataFrame()
    df["volume"] = df.get("volume", 0)
    return df.sort_values("date").drop_duplicates("date")


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def enrich_features(df: pd.DataFrame, spy: pd.Series, soxx: pd.Series | None, sym: str) -> pd.DataFrame:
    out = df.copy()
    c = out["close"].astype(float)
    out["r1"] = c.pct_change(1)
    out["r5"] = c.pct_change(5)
    spy_a = spy.reindex(out["date"]).ffill()
    out["spy_r5"] = spy_a.pct_change(5).values
    out["residual"] = out["r5"] - out["spy_r5"]
    if soxx is not None and sym in semi_symbols():
        soxx_a = soxx.reindex(out["date"]).ffill()
        out["semi_lead"] = out["r5"] - soxx_a.pct_change(5).values
    else:
        out["semi_lead"] = 0.0
    out["rsi"] = _rsi(c).values
    hl = (out["high"] - out["low"]).rolling(10).mean()
    out["atr"] = hl.values
    out["score"] = (
        0.45 * (out["r5"] * 200)
        + 0.25 * (out["r1"] * 400)
        + 0.2 * (out["residual"] * 150)
        + 0.1 * ((out["rsi"] - 50) / 50)
    )
    if sym in semi_symbols():
        out["score"] += 0.08 * (out["semi_lead"] * 120)
    out["score"] = out["score"].clip(-1, 1)
    return out


def _target_usd(price: float, atr: float, mult: float = 1.0, thin: bool = False) -> float:
    base = max(min_target_usd(), price * min_target_pct(), atr_k() * atr if atr > 0 else 0)
    base *= mult
    if thin:
        base *= 1.35
    return round(float(base), 4)


def _stop_usd(target: float) -> float:
    raw = max(target * stop_target_mult(), 0.25)
    return min(raw, max_stop_usd())


def _pattern_signal(row: pd.Series, cfg: SimConfig, sym: str, el: float, es: float) -> tuple[str | None, str | None]:
    score = float(row["score"])
    r1 = float(row["r1"])
    r5 = float(row["r5"])
    if score <= es and r5 < 0 and r1 > -0.0015:
        return "rip_fade", "short"
    if score >= el and r5 > 0 and r1 < 0.0015:
        return "pullback_uptrend", "long"
    if score >= el + 0.12 and r5 > 0.0008:
        return "momentum_long", "long"
    if score <= es - 0.12 and r5 < -0.0008:
        return "momentum_short", "short"
    return None, None


def _simulate_symbol(
    sym: str,
    df: pd.DataFrame,
    cfg: SimConfig,
    *,
    el: float,
    es: float,
    target_mult: float,
    short_spy_filter: float,
    allowed_patterns: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, PatternStats]]:
    thin = sym in thin_etf_symbols()
    trades: list[dict[str, Any]] = []
    pstats = {p: PatternStats() for p in _PATTERNS}
    i = 20
    n = len(df)
    while i < n - 1:
        row = df.iloc[i]
        pattern, side = _pattern_signal(row, cfg, sym, el, es)
        if not pattern or not side:
            i += 1
            continue
        if allowed_patterns and pattern not in allowed_patterns:
            i += 1
            continue
        spy_r5 = float(row["spy_r5"]) if pd.notna(row["spy_r5"]) else 0.0
        if side == "short" and short_spy_filter > 0 and spy_r5 > short_spy_filter:
            i += 1
            continue
        entry_px = float(row["close"])
        atr = float(row["atr"]) if pd.notna(row["atr"]) else entry_px * 0.01
        tgt = _target_usd(entry_px, atr, target_mult, thin)
        stop = _stop_usd(tgt)
        exit_reason = "timeout"
        pnl = 0.0
        hold = 0
        for j in range(i + 1, min(i + 1 + cfg.max_hold_bars, n)):
            px = float(df.iloc[j]["close"])
            hold += 1
            if side == "long":
                u = px - entry_px
            else:
                u = entry_px - px
            if u >= tgt:
                pnl = u
                exit_reason = "target_hit"
                i = j + 1
                break
            if u <= -stop:
                pnl = u
                exit_reason = "stop_loss"
                i = j + 1
                break
        else:
            px = float(df.iloc[min(i + cfg.max_hold_bars, n - 1)]["close"])
            pnl = (px - entry_px) if side == "long" else (entry_px - px)
            i = min(i + cfg.max_hold_bars, n - 1) + 1
        rec = {
            "date": str(row["date"].date()),
            "pattern": pattern,
            "side": side,
            "pnl_usd": round(pnl, 4),
            "exit": exit_reason,
            "hold_bars": hold or cfg.max_hold_bars,
        }
        trades.append(rec)
        ps = pstats[pattern]
        ps.trades += 1
        ps.sum_pnl_usd += pnl
        if pnl >= 0:
            ps.wins += 1
        if exit_reason == "stop_loss":
            ps.stop_loss += 1
        elif exit_reason == "target_hit":
            ps.target_hit += 1
    return trades, pstats


def _summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "sum_pnl_usd": 0.0, "win_rate": 0.0, "avg_pnl": 0.0}
    pnls = [t["pnl_usd"] for t in trades]
    wins = sum(1 for p in pnls if p >= 0)
    return {
        "trades": len(trades),
        "sum_pnl_usd": round(sum(pnls), 4),
        "win_rate": round(wins / len(pnls), 3),
        "avg_pnl": round(sum(pnls) / len(pnls), 4),
        "median_pnl": round(float(np.median(pnls)), 4),
    }


def _grid_search_train(sym: str, train_df: pd.DataFrame, cfg: SimConfig) -> dict[str, Any]:
    thin = sym in thin_etf_symbols()
    el0 = cfg.thin_enter_long if thin else cfg.enter_long_base
    es0 = cfg.thin_enter_short if thin else cfg.enter_short_base
    best: dict[str, Any] = {"score": -1e9, "params": {}}
    for el_d in (-0.04, -0.02, 0.0, 0.02, 0.04):
        for es_d in (-0.04, -0.02, 0.0, 0.02, 0.04):
            for tm in (0.85, 1.0, 1.15):
                for ssf in (0.0, 0.0002, 0.0005):
                    el = el0 + el_d
                    es = es0 + es_d
                    trades, pstats = _simulate_symbol(
                        sym, train_df, cfg, el=el, es=es, target_mult=tm, short_spy_filter=ssf
                    )
                    s = _summarize(trades)
                    if s["trades"] < 30:
                        continue
                    # Prefer positive expectancy with enough trades
                    score = s["sum_pnl_usd"] + s["avg_pnl"] * 10
                    if score > best["score"]:
                        pat_pnls = {p: st.sum_pnl_usd for p, st in pstats.items() if st.trades >= 5}
                        disable = {p for p, v in pat_pnls.items() if v < -2.0}
                        best = {
                            "score": score,
                            "params": {
                                "enter_long_delta": round(el_d, 3),
                                "enter_short_delta": round(es_d, 3),
                                "target_mult": tm,
                                "short_spy_filter": ssf,
                                "disable_patterns": sorted(disable),
                            },
                            "train": s,
                            "pattern_pnl": {p: round(st.sum_pnl_usd, 2) for p, st in pstats.items()},
                        }
    return best


def verify_symbol(
    sym: str,
    spy_df: pd.DataFrame,
    soxx_df: pd.DataFrame | None,
    cfg: SimConfig,
) -> dict[str, Any]:
    raw = fetch_daily(sym, cfg.years)
    if raw.empty or len(raw) < 252:
        return {"symbol": sym, "ok": False, "error": "insufficient_history", "rows": len(raw)}

    spy_s = spy_df.set_index("date")["close"].astype(float)
    soxx_s = soxx_df.set_index("date")["close"].astype(float) if soxx_df is not None and not soxx_df.empty else None
    df = enrich_features(raw, spy_s, soxx_s, sym)
    df = df.dropna(subset=["r5", "score"]).reset_index(drop=True)

    split = int(len(df) * cfg.train_frac)
    train_df = df.iloc[:split].reset_index(drop=True)
    test_df = df.iloc[split:].reset_index(drop=True)

    best = _grid_search_train(sym, train_df, cfg)
    if not best.get("params"):
        # fallback defaults
        thin = sym in thin_etf_symbols()
        best = {
            "params": {
                "enter_long_delta": 0.0,
                "enter_short_delta": 0.0,
                "target_mult": 1.0,
                "short_spy_filter": 0.0,
                "disable_patterns": [],
            },
            "train": _summarize([]),
        }

    p = best["params"]
    thin = sym in thin_etf_symbols()
    el = (cfg.thin_enter_long if thin else cfg.enter_long_base) + float(p["enter_long_delta"])
    es = (cfg.thin_enter_short if thin else cfg.enter_short_base) + float(p["enter_short_delta"])
    allowed = set(_PATTERNS) - set(p.get("disable_patterns") or [])

    test_trades, test_pstats = _simulate_symbol(
        sym,
        test_df,
        cfg,
        el=el,
        es=es,
        target_mult=float(p["target_mult"]),
        short_spy_filter=float(p["short_spy_filter"]),
        allowed_patterns=allowed if allowed else None,
    )
    full_trades, full_pstats = _simulate_symbol(
        sym,
        df,
        cfg,
        el=el,
        es=es,
        target_mult=float(p["target_mult"]),
        short_spy_filter=float(p["short_spy_filter"]),
        allowed_patterns=allowed if allowed else None,
    )

    long_pnl = sum(t["pnl_usd"] for t in full_trades if t["side"] == "long")
    short_pnl = sum(t["pnl_usd"] for t in full_trades if t["side"] == "short")

    recommendations: list[str] = []
    if long_pnl > 0.5 and short_pnl < -0.5:
        recommendations.append("favor_long_patterns")
        p.setdefault("score_bias", 0.03)
    elif short_pnl > 0.5 and long_pnl < -0.5:
        recommendations.append("favor_short_patterns")
        p.setdefault("score_bias", -0.03)
    if float(p.get("short_spy_filter") or 0) > 0:
        recommendations.append("use_short_spy_filter")
    if float(p.get("target_mult") or 1) < 0.95:
        recommendations.append("tighter_targets")
    elif float(p.get("target_mult") or 1) > 1.05:
        recommendations.append("wider_targets")
    for pat, st in full_pstats.items():
        if st.trades >= 10 and st.sum_pnl_usd < -1.0:
            recommendations.append(f"disable_{pat}")
    for pat, st in full_pstats.items():
        if st.trades >= 10 and st.sum_pnl_usd > 1.0:
            recommendations.append(f"emphasize_{pat}")

    return {
        "symbol": sym,
        "ok": True,
        "history_start": str(df["date"].iloc[0].date()),
        "history_end": str(df["date"].iloc[-1].date()),
        "bars": len(df),
        "train_bars": len(train_df),
        "test_bars": len(test_df),
        "recommended_params": p,
        "train_summary": best.get("train") or _summarize([]),
        "test_summary": _summarize(test_trades),
        "full_summary": _summarize(full_trades),
        "pattern_full": {
            p: {
                "trades": st.trades,
                "wins": st.wins,
                "sum_pnl_usd": round(st.sum_pnl_usd, 2),
                "win_rate": round(st.wins / st.trades, 3) if st.trades else 0,
            }
            for p, st in full_pstats.items()
        },
        "side_pnl": {"long": round(long_pnl, 2), "short": round(short_pnl, 2)},
        "recommendations": recommendations,
        "note": "Daily-bar proxy; calibrate live 1m params from direction/pattern edges only.",
    }


def verify_universe(years: int = 10, out_path: Path | None = None) -> dict[str, Any]:
    cfg = SimConfig(years=years)
    spy_df = fetch_daily("SPY", years)
    soxx_df = fetch_daily("SOXX", years)
    if spy_df.empty:
        return {"ok": False, "error": "SPY history unavailable"}

    results = []
    for sym in universe():
        try:
            results.append(verify_symbol(sym, spy_df, soxx_df, cfg))
        except Exception as e:
            results.append({"symbol": sym, "ok": False, "error": f"{type(e).__name__}:{e}"})

    report = {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "years": years,
        "method": "daily_proxy_skim_patterns",
        "caveat": "1-minute skim cannot be replayed 10-20y on free data; daily bars proxy pattern/regime edges.",
        "symbols": results,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def apply_recommendations_to_learned(report: dict[str, Any]) -> list[str]:
    """Seed learned/*.json params from historical verification (non-destructive merge)."""
    from agents.skim_swarm.symbol_learning import load_learned, save_learned

    applied: list[str] = []
    for row in report.get("symbols") or []:
        if not row.get("ok"):
            continue
        sym = str(row["symbol"])
        rec = row.get("recommended_params") or {}
        L = load_learned(sym)
        params = L.setdefault("params", {})
        for k in ("enter_long_delta", "enter_short_delta", "target_mult", "short_spy_filter", "score_bias"):
            if k in rec and rec[k] is not None:
                params[k] = rec[k]
        notes = L.setdefault("notes", [])
        notes.append(f"historical_verify:{row.get('history_start')}->{row.get('history_end')}")
        L["notes"] = notes[-12:]
        L["historical_verify"] = {
            "ts": report.get("ts"),
            "test_summary": row.get("test_summary"),
            "recommendations": row.get("recommendations"),
            "recommended_params": rec,
        }
        save_learned(sym, L)
        applied.append(sym)
    return applied
