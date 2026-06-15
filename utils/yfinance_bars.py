"""Normalize yfinance intraday DataFrames for swarm feature code."""
from __future__ import annotations

import pandas as pd


def flatten_intraday_df(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Flatten yfinance 1m output to columns Open, High, Low, Close, Volume."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        sym = (symbol or "").strip().upper()
        names = list(df.columns.names or [])
        if sym and sym in df.columns.get_level_values(-1):
            df = df.xs(sym, axis=1, level=-1)
        elif len(names) >= 1 and names[0] == "Price":
            df.columns = [str(c[0]) for c in df.columns]
        else:
            df.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in df.columns]
    else:
        df.columns = [str(c).strip() for c in df.columns]
    lower = {str(c).lower(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for std in ("Open", "High", "Low", "Close", "Volume"):
        src = lower.get(std.lower())
        if src is not None:
            out[std] = pd.to_numeric(df[src], errors="coerce")
    if "Close" not in out.columns or out["Close"].dropna().empty:
        return pd.DataFrame()
    return out
