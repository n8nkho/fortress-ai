"""Compute technical features and market regime labels; write features/*.csv."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agents.historical_seeder.paths import features_dir, prices_dir, repo_root

logger = logging.getLogger("historical_seeder.feature_engine")

def _read_price_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    for c in ("open", "high", "low", "close", "adj_close", "volume"):
        if c not in df.columns:
            df[c] = np.nan if c != "volume" else 0
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce").fillna(df["close"])
    return df


def _returns(close: pd.Series) -> pd.Series:
    return close.pct_change()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _vol_ann(ret: pd.Series, window: int = 20) -> pd.Series:
    return ret.rolling(window, min_periods=2).std() * np.sqrt(252.0)


def _build_regime_series(spy: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """Align SPY + VIX and assign regime per rules."""
    s = spy[["date", "close"]].rename(columns={"close": "spy_close"}).copy()
    v = vix[["date", "close"]].rename(columns={"close": "vix_close"}).copy()
    m = s.merge(v, on="date", how="inner").sort_values("date")
    m["returns_1d"] = m["spy_close"].pct_change()
    m["returns_20d"] = m["spy_close"].pct_change(20)
    m["ma_50"] = m["spy_close"].rolling(50, min_periods=40).mean()
    m["ma_200"] = m["spy_close"].rolling(200, min_periods=130).mean()

    def classify_row(row: pd.Series) -> str:
        vix_val = float(row["vix_close"]) if pd.notna(row["vix_close"]) else np.nan
        spy_c = float(row["spy_close"])
        ma2 = row["ma_200"]
        r20 = row["returns_20d"]
        if pd.isna(vix_val):
            return "NEUTRAL_RANGING"
        if vix_val > 30:
            return "HIGH_VOL"
        if pd.notna(ma2) and spy_c < ma2 and vix_val > 25:
            return "BEAR"
        if pd.notna(ma2) and spy_c > ma2 and vix_val < 20 and pd.notna(r20) and r20 > 0:
            return "BULL"
        return "NEUTRAL_RANGING"

    m["regime"] = m.apply(classify_row, axis=1)
    return m[["date", "regime"]]


def enrich_dataframe(df: pd.DataFrame, regime_lookup: pd.DataFrame | None, _symbol_label: str = "") -> pd.DataFrame:
    out = df.copy()
    c = out["adj_close"].fillna(out["close"])
    out["returns_1d"] = _returns(c) * 100.0
    out["returns_5d"] = c.pct_change(5) * 100.0
    out["returns_20d"] = c.pct_change(20) * 100.0
    out["rsi_14"] = _rsi(c, 14)
    atr = _atr(out["high"], out["low"], c, 14)
    out["atr_14"] = (atr / c.replace(0, np.nan)).fillna(0.0)
    r1 = _returns(c)
    out["vol_20d"] = _vol_ann(r1, 20)
    out["ma_50"] = c.rolling(50, min_periods=40).mean()
    out["ma_200"] = c.rolling(200, min_periods=130).mean()
    out["above_ma50"] = (c > out["ma_50"]).fillna(False)
    out["above_ma200"] = (c > out["ma_200"]).fillna(False)
    if regime_lookup is not None:
        out = out.merge(regime_lookup, on="date", how="left")
        out["regime"] = out["regime"].fillna("NEUTRAL_RANGING")
    else:
        out["regime"] = "NEUTRAL_RANGING"
    return out


def run_features(*, symbols_filter: set[str] | None = None) -> dict[str, Any]:
    prices_dir().mkdir(parents=True, exist_ok=True)
    features_dir().mkdir(parents=True, exist_ok=True)

    spy_path = prices_dir() / "SPY_daily.csv"
    vix_path = prices_dir() / "VIX_daily.csv"
    if not spy_path.exists() or not vix_path.exists():
        logger.error("missing SPY or VIX prices — run price_loader first")
        return {"error": "missing_prices", "written": []}

    spy = _read_price_csv(spy_path)
    vix = _read_price_csv(vix_path)
    regime_lookup = _build_regime_series(spy, vix)

    written: list[str] = []
    for csv in sorted(prices_dir().glob("*_daily.csv")):
        stem = csv.stem.replace("_daily", "")
        if symbols_filter and stem not in symbols_filter:
            continue
        df = _read_price_csv(csv)
        enriched = enrich_dataframe(df, regime_lookup, stem)
        outp = features_dir() / f"{stem}_features.csv"
        enriched.to_csv(outp, index=False)
        written.append(str(outp.relative_to(repo_root())))
        logger.info("wrote %s rows -> %s", len(enriched), outp.name)

    return {"written": written, "regime_rows": len(regime_lookup)}
