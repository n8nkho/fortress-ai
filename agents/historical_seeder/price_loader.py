"""Download daily OHLCV via yfinance into data/historical/prices/."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from agents.historical_seeder.paths import prices_dir, repo_root

logger = logging.getLogger("historical_seeder.price_loader")

CORE = ["SPY", "QQQ", "IWM", "DIA", "VTI"]
SECTOR = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLRE", "XLP", "XLY", "XLB"]
# Macro names on disk; Yahoo tickers may differ
MACRO_MAP = {
    "VIX": "^VIX",
    "TLT": "TLT",
    "GLD": "GLD",
    "USO": "USO",
    "DXY": "DX-Y.NYB",
}

START_DEFAULT = "2000-01-01"
VIX_LONG_START = "1990-01-01"


def _yahoo_download(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        ticker,
        start=start,
        end=end,
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).strip() for c in df.columns.values]
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "adj_close" not in df.columns and "close" in df.columns:
        df["adj_close"] = df["close"]
    for need in ("open", "high", "low", "close", "volume"):
        if need not in df.columns:
            if need == "volume":
                df["volume"] = 0
            else:
                logger.warning("missing column %s for %s", need, ticker)
    if "adj_close" not in df.columns and "close" in df.columns:
        df["adj_close"] = df["close"]
    df = df.reset_index()
    if "Date" in df.columns:
        df = df.rename(columns={"Date": "date"})
    elif "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    cols = ["date", "open", "high", "low", "close", "volume", "adj_close"]
    for c in cols:
        if c not in df.columns:
            df[c] = float("nan") if c != "volume" else 0
    return df[cols]


def _file_prefix(symbol: str) -> str:
    return symbol.replace("^", "").replace("-", "_")


def _out_path(symbol_key: str) -> Path:
    return prices_dir() / f"{symbol_key}_daily.csv"


def _already_fresh(path: Path, *, utc_day: date | None = None) -> bool:
    if not path.exists():
        return False
    day = utc_day or datetime.now(timezone.utc).date()
    m = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()
    return m == day


def download_symbol(
    symbol_key: str,
    yahoo_ticker: str,
    start: str,
    end: str | None = None,
    *,
    skip_if_today: bool = True,
) -> Path:
    prices_dir().mkdir(parents=True, exist_ok=True)
    outp = _out_path(symbol_key)
    if skip_if_today and _already_fresh(outp):
        logger.info("skip download (already fresh today): %s -> %s", yahoo_ticker, outp)
        return outp
    logger.info("download %s as %s", yahoo_ticker, outp.name)
    df = _yahoo_download(yahoo_ticker, start, end)
    if df.empty:
        logger.error("empty dataframe for %s", yahoo_ticker)
    df.to_csv(outp, index=False)
    return outp


def download_all_common(*, skip_if_today: bool = True) -> list[Path]:
    """Core + sector + macro (2000+)."""
    out: list[Path] = []
    tickers: list[tuple[str, str]] = [(s, s) for s in CORE + SECTOR]
    for key, yf_t in MACRO_MAP.items():
        tickers.append((key, yf_t))

    for sym_key, yf_t in tickers:
        out.append(download_symbol(sym_key, yf_t, START_DEFAULT, skip_if_today=skip_if_today))
    return out


def download_vix_long_history(*, skip_if_today: bool = True) -> Path:
    """^VIX back to 1990 into VIX_daily.csv (canonical name)."""
    prices_dir().mkdir(parents=True, exist_ok=True)
    outp = prices_dir() / "VIX_daily.csv"
    if skip_if_today and _already_fresh(outp):
        logger.info("skip VIX long download (fresh today)")
        return outp
    df = _yahoo_download("^VIX", VIX_LONG_START)
    if df.empty:
        logger.error("VIX long download empty")
    df.to_csv(outp, index=False)
    return outp


def run_downloads(*, skip_if_today: bool = True) -> dict[str, Any]:
    paths = download_all_common(skip_if_today=skip_if_today)
    vixp = download_vix_long_history(skip_if_today=skip_if_today)
    return {"paths": paths + [vixp], "root": str(repo_root())}
