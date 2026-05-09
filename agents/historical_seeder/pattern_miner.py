"""Mine historical patterns from feature CSVs; write pattern_results.json."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from agents.historical_seeder.paths import features_dir, pattern_results_path

logger = logging.getLogger("historical_seeder.pattern_miner")


def _read_feat(sym: str) -> pd.DataFrame:
    p = features_dir() / f"{sym}_features.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def _fwd_ret(close: pd.Series, days: int) -> pd.Series:
    """Percent forward return from t to t+days (using shift negative on future)."""
    fw = close.shift(-days) / close - 1.0
    return fw * 100.0


def _laplace(wins: int, n: int) -> float:
    return (wins + 1) / (n + 2)


def _best_regime(df: pd.DataFrame, regime_col: str = "regime") -> str:
    if df.empty or regime_col not in df.columns:
        return "UNKNOWN"
    best_r = ""
    best_wr = -1.0
    for reg, g in df.groupby(regime_col):
        if len(g) < 5:
            continue
        wins = int((g["fwd_win"] > 0).sum()) if "fwd_win" in g.columns else 0
        wr = wins / max(len(g), 1)
        if wr > best_wr:
            best_wr = wr
            best_r = str(reg)
    return best_r or str(df[regime_col].mode().iloc[0]) if len(df) else "UNKNOWN"


def mine_all() -> dict[str, Any]:
    features_dir()
    spy = _read_feat("SPY")
    qqq = _read_feat("QQQ")
    iwm = _read_feat("IWM")
    vix = _read_feat("VIX")

    results: dict[str, Any] = {"generated_at": datetime.utcnow().isoformat() + "Z", "patterns": {}}

    # --- PATTERN 1: RSI < 30 mean reversion ---
    pool = []
    for name, df in [("SPY", spy), ("QQQ", qqq), ("IWM", iwm)]:
        if df.empty:
            continue
        c = df["adj_close"].fillna(df["close"])
        df = df.copy()
        df["fwd5"] = _fwd_ret(c, 5)
        df["fwd20"] = _fwd_ret(c, 20)
        df["sym"] = name
        sub = df[df["rsi_14"] < 30].copy()
        sub["fwd_win"] = (sub["fwd5"] > 0).astype(int)
        pool.append(sub)
    if pool:
        allp = pd.concat(pool, ignore_index=True)
        allp = allp.dropna(subset=["fwd5", "fwd20"])
        wins = int((allp["fwd5"] > 0).sum())
        n = len(allp)
        allp["fwd_win"] = (allp["fwd5"] > 0).astype(int)
        br = _best_regime(allp)
        results["patterns"]["mean_reversion_rsi_extremes"] = {
            "description": "RSI(14)<30 on SPY/QQQ/IWM; forward returns",
            "total_occurrences": n,
            "win_count": wins,
            "loss_count": n - wins,
            "avg_return_5d": float(allp["fwd5"].mean()) if n else 0.0,
            "avg_return_20d": float(allp["fwd20"].mean()) if n else 0.0,
            "win_rate": wins / n if n else 0.0,
            "best_regime": br,
            "confidence_score": _laplace(wins, n),
            "by_regime_win_rate": {
                str(k): float(v["fwd_win"].mean()) if len(v) >= 5 else None
                for k, v in allp.groupby("regime")
            },
        }

    # --- PATTERN 2: VIX 1d spike > 20% ---
    if not vix.empty and not spy.empty:
        vc = vix["adj_close"].fillna(vix["close"])
        vix = vix.copy()
        vix["vix_ret_1d"] = vc.pct_change() * 100.0
        merged = spy[["date", "adj_close", "close"]].rename(columns={"adj_close": "spy_ac", "close": "spy_c"})
        merged = merged.merge(vix[["date", "vix_ret_1d"]], on="date", how="inner")
        c = merged["spy_ac"].fillna(merged["spy_c"])
        merged["fwd5_spy"] = _fwd_ret(c, 5)
        spike = merged[merged["vix_ret_1d"] > 20.0].dropna(subset=["fwd5_spy"])
        n2 = len(spike)
        w2 = int((spike["fwd5_spy"] > 0).sum())
        sp2 = spike.merge(spy[["date", "regime"]], on="date", how="left")
        sp2["fwd_win"] = (sp2["fwd5_spy"] > 0).astype(int)
        br2 = _best_regime(sp2) if n2 else "UNKNOWN"
        results["patterns"]["vix_spike_reversion"] = {
            "description": "VIX 1-day return > +20%; SPY 5d forward",
            "total_occurrences": n2,
            "win_count": w2,
            "loss_count": n2 - w2,
            "avg_return_5d": float(spike["fwd5_spy"].mean()) if n2 else 0.0,
            "avg_return_20d": None,
            "win_rate": w2 / n2 if n2 else 0.0,
            "best_regime": br2,
            "confidence_score": _laplace(w2, n2),
        }

    # --- PATTERN 3: Golden / Death cross ---
    if not spy.empty:
        s = spy.copy()
        c = s["adj_close"].fillna(s["close"])
        s["ma50_prev"] = s["ma_50"].shift(1)
        s["ma200_prev"] = s["ma_200"].shift(1)
        s["golden"] = (s["ma50_prev"] <= s["ma200_prev"]) & (s["ma_50"] > s["ma_200"])
        s["death"] = (s["ma50_prev"] >= s["ma200_prev"]) & (s["ma_50"] < s["ma_200"])
        s["cross_evt"] = np.where(s["golden"], "golden", np.where(s["death"], "death", ""))
        s["fwd60"] = _fwd_ret(c, 60)
        ev = s[s["cross_evt"] != ""].copy()
        ev = ev.dropna(subset=["fwd60"])
        ev["fwd_win"] = (ev["fwd60"] > 0).astype(int)
        n3 = len(ev)
        w3 = int((ev["fwd60"] > 0).sum())
        results["patterns"]["ma_cross_60d"] = {
            "description": "MA50 vs MA200 cross; SPY 60d forward return",
            "total_occurrences": n3,
            "win_count": w3,
            "loss_count": n3 - w3,
            "avg_return_5d": None,
            "avg_return_20d": None,
            "avg_return_60d": float(ev["fwd60"].mean()) if n3 else 0.0,
            "win_rate": w3 / n3 if n3 else 0.0,
            "best_regime": _best_regime(ev.merge(spy[["date", "regime"]], on="date")),
            "confidence_score": _laplace(w3, n3),
            "by_cross": {
                "golden": int(ev.loc[ev["cross_evt"] == "golden", "fwd_win"].sum()),
                "death": int(ev.loc[ev["cross_evt"] == "death", "fwd_win"].sum()),
            },
        }

    # --- PATTERN 4: Sector rotation ---
    sectors = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLRE", "XLP", "XLY", "XLB"]
    sec_rows = []
    if not spy.empty:
        spy_aux = spy[["date", "rsi_14"]].copy()
        sc = spy["adj_close"].fillna(spy["close"])
        spy_aux["fwd20_spy"] = _fwd_ret(sc, 20)
        spy_aux = spy_aux.rename(columns={"rsi_14": "spy_rsi"})
        for sec in sectors:
            df = _read_feat(sec)
            if df.empty:
                continue
            csec = df["adj_close"].fillna(df["close"])
            df = df.merge(spy_aux, on="date", how="inner")
            df["fwd20_sec"] = _fwd_ret(csec, 20)
            m = df
            m["excess_20d"] = m["fwd20_sec"] - m["fwd20_spy"]
            setup = m[(m["rsi_14"] < 35) & (m["spy_rsi"] > 45)]
            setup = setup.dropna(subset=["excess_20d"])
            if setup.empty:
                continue
            wins = int((setup["excess_20d"] > 0).sum())
            n = len(setup)
            sec_rows.append(
                {
                    "sector": sec,
                    "total_occurrences": n,
                    "win_count": wins,
                    "loss_count": n - wins,
                    "avg_excess_return_20d": float(setup["excess_20d"].mean()),
                    "win_rate": wins / n if n else 0.0,
                    "confidence_score": _laplace(wins, n),
                    "best_regime": _best_regime(setup.assign(fwd_win=(setup["excess_20d"] > 0).astype(int))),
                }
            )
        results["patterns"]["sector_rotation_rsi_divergence"] = {"by_sector": sec_rows}

    # --- PATTERN 5: weekday seasonality SPY ---
    if not spy.empty:
        s = spy.copy()
        c = s["adj_close"].fillna(s["close"])
        s["dow"] = pd.to_datetime(s["date"]).dt.dayofweek
        s["dret"] = c.pct_change() * 100.0
        g = s.dropna(subset=["dret"]).groupby("dow")["dret"]
        gm = g.mean()
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        means = {dow_names[i]: float(gm.loc[i]) if i in gm.index else float("nan") for i in range(5)}
        overall = float(s["dret"].mean())
        vals = [v for v in means.values() if v == v]
        rng = float(max(vals) - min(vals)) if vals else 0.0
        results["patterns"]["weekday_seasonality_spy"] = {
            "mean_daily_return_pct_by_dow": means,
            "overall_mean_daily_pct": overall,
            "range_max_min_pct": rng,
            "note": "Edge significance not formally tested; inspect range vs noise",
        }

    # --- PATTERN 6: post-drawdown recovery ---
    if not spy.empty:
        s = spy.copy()
        c = s["adj_close"].fillna(s["close"])
        s["fwd60"] = _fwd_ret(c, 60)
        s = s[s["returns_20d"] < -10.0].dropna(subset=["fwd60"])
        s["fwd_win"] = (s["fwd60"] > 0).astype(int)
        n6 = len(s)
        w6 = int((s["fwd60"] > 0).sum())
        results["patterns"]["post_drawdown_recovery"] = {
            "description": "SPY 20d return < -10%",
            "total_occurrences": n6,
            "win_count": w6,
            "loss_count": n6 - w6,
            "avg_return_60d": float(s["fwd60"].mean()) if n6 else 0.0,
            "win_rate": w6 / n6 if n6 else 0.0,
            "confidence_score": _laplace(w6, n6),
            "best_regime": _best_regime(s),
        }

    # --- PATTERN 7: MR under high VIX vs low VIX ---
    if not spy.empty and not vix.empty:
        s = spy.merge(vix[["date", "close"]].rename(columns={"close": "vix_level"}), on="date", how="inner")
        c = s["adj_close"].fillna(s["close"])
        s["fwd5"] = _fwd_ret(c, 5)
        mr = s[s["rsi_14"] < 30].dropna(subset=["fwd5", "vix_level"])
        high = mr[mr["vix_level"] > 25]
        low = mr[mr["vix_level"] < 20]
        results["patterns"]["mean_reversion_high_vol_vs_calm"] = {
            "setup": "SPY RSI<30",
            "high_vix_gt25": {
                "n": len(high),
                "avg_fwd5_pct": float(high["fwd5"].mean()) if len(high) else None,
                "win_rate": float((high["fwd5"] > 0).mean()) if len(high) else None,
            },
            "low_vix_lt20": {
                "n": len(low),
                "avg_fwd5_pct": float(low["fwd5"].mean()) if len(low) else None,
                "win_rate": float((low["fwd5"] > 0).mean()) if len(low) else None,
            },
        }

    # --- PATTERN 8: earnings months ---
    if not spy.empty:
        s = spy.copy()
        c = s["adj_close"].fillna(s["close"])
        s["month"] = pd.to_datetime(s["date"]).dt.month
        s["dret"] = c.pct_change() * 100.0
        s = s.dropna(subset=["dret"])
        earn = s[s["month"].isin([1, 4, 7, 10])]["dret"].mean()
        other = s[~s["month"].isin([1, 4, 7, 10])]["dret"].mean()
        results["patterns"]["earnings_month_effect"] = {
            "mean_daily_return_pct_earn_months": float(earn),
            "mean_daily_return_pct_other": float(other),
            "delta_pct": float(earn - other),
        }

    pattern_results_path().parent.mkdir(parents=True, exist_ok=True)
    pattern_results_path().write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    logger.info("wrote %s", pattern_results_path())
    return results
