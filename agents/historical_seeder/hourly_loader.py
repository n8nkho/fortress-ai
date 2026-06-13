"""Download hourly OHLCV (5-year chunks) for market memory / consciousness inputs."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from agents.historical_seeder.paths import hourly_dir, repo_root

logger = logging.getLogger("historical_seeder.hourly_loader")

DEFAULT_SYMBOLS: dict[str, str] = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "SMH": "SMH",
    "IWM": "IWM",
}

# yfinance 1h interval: only last ~730 calendar days available
_YF_HOURLY_MAX_DAYS = 729
_CHUNK_DAYS = 60


def _years_back() -> int:
    import os

    try:
        return max(1, min(10, int(os.environ.get("FORTRESS_HOURLY_KNOWLEDGE_YEARS", "5") or 5)))
    except ValueError:
        return 5


def _normalize_hourly_df(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).strip().lower() for c in df.columns.values]
    else:
        df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.reset_index()
    ts_col = "datetime" if "datetime" in df.columns else ("date" if "date" in df.columns else df.columns[0])
    df = df.rename(columns={ts_col: "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        if c not in df.columns:
            df[c] = float("nan") if c != "volume" else 0
    df = df.dropna(subset=["ts", "close"]).sort_values("ts")
    return df[["ts", "open", "high", "low", "close", "volume"]]


def _download_chunk(yahoo_ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        yahoo_ticker,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1h",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    return _normalize_hourly_df(df)


def download_symbol_hourly(
    symbol_key: str,
    yahoo_ticker: str,
    *,
    years: int | None = None,
    force: bool = False,
) -> Path:
    """Fetch up to `years` of hourly bars in bounded chunks; write CSV."""
    hourly_dir().mkdir(parents=True, exist_ok=True)
    outp = hourly_dir() / f"{symbol_key}_hourly.csv"
    yrs = years if years is not None else _years_back()
    if outp.exists() and not force:
        age_h = (datetime.now(timezone.utc).timestamp() - outp.stat().st_mtime) / 3600.0
        if age_h < 24:
            logger.info("skip hourly download (fresh): %s", outp.name)
            return outp

    end = datetime.now(timezone.utc)
    max_days = min(int(365.25 * yrs), _YF_HOURLY_MAX_DAYS)
    start = end - timedelta(days=max_days)
    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=_CHUNK_DAYS), end)
        try:
            part = _download_chunk(yahoo_ticker, cursor, chunk_end)
            if not part.empty:
                frames.append(part)
                logger.info(
                    "hourly chunk %s %s -> %s rows",
                    yahoo_ticker,
                    cursor.date(),
                    len(part),
                )
        except Exception as e:
            logger.warning("hourly chunk failed %s %s: %s", yahoo_ticker, cursor.date(), e)
        cursor = chunk_end

    if not frames:
        logger.error("no hourly data for %s", yahoo_ticker)
        if outp.exists():
            return outp
        outp.write_text("ts,open,high,low,close,volume\n", encoding="utf-8")
        return outp

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts"], keep="last").sort_values("ts")
    merged.to_csv(outp, index=False)
    logger.info("wrote %s rows -> %s", len(merged), outp)
    return outp


def run_hourly_downloads(
    *,
    symbols: dict[str, str] | None = None,
    years: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    syms = symbols or DEFAULT_SYMBOLS
    paths: list[str] = []
    for key, yf_t in syms.items():
        p = download_symbol_hourly(key, yf_t, years=years, force=force)
        paths.append(str(p.relative_to(repo_root())))
    return {
        "paths": paths,
        "years": years or _years_back(),
        "hourly_window_days": _YF_HOURLY_MAX_DAYS,
        "symbols": list(syms.keys()),
    }
