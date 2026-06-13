"""Find historical sessions most similar to today's macro fingerprint."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

_ET = ZoneInfo("America/New_York")
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def _years() -> int:
    try:
        return max(1, min(10, int(os.environ.get("FORTRESS_HOURLY_KNOWLEDGE_YEARS", "5") or 5)))
    except ValueError:
        return 5


def _read_daily(symbol: str) -> pd.DataFrame:
    from agents.historical_seeder.paths import prices_dir

    p = prices_dir() / f"{symbol}_daily.csv"
    if not p.is_file():
        return pd.DataFrame()
    df = pd.read_csv(p, parse_dates=["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["date", "close"]).sort_values("date")


def _build_fingerprint_df(spy: pd.DataFrame, vix: pd.DataFrame | None) -> pd.DataFrame:
    if spy.empty or len(spy) < 22:
        return pd.DataFrame()
    df = spy.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ret_1d"] = df["close"].pct_change() * 100.0
    df["ret_5d"] = df["close"].pct_change(5) * 100.0
    df["weekday"] = df["date"].dt.dayofweek
    if vix is not None and not vix.empty:
        v = vix[["date", "close"]].rename(columns={"close": "vix"})
        df = df.merge(v, on="date", how="left")
    else:
        df["vix"] = 20.0
    df["vix"] = df["vix"].fillna(20.0)
    cutoff = df["date"].max() - pd.Timedelta(days=int(365.25 * _years()))
    return df[df["date"] >= cutoff].dropna(subset=["ret_1d"])


def _today_fingerprint(live: dict[str, Any] | None = None) -> dict[str, float]:
    live = live or {}
    now = datetime.now(_ET)
    return {
        "weekday": float(now.weekday()),
        "ret_1d": float(live.get("change_1d_pct") or 0.0),
        "ret_5d": float(live.get("change_5d_pct") or 0.0),
        "vix": float(live.get("vix_last") or live.get("vix") or 20.0),
    }


def find_analogue_days(*, k: int = 5, live_tape: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """k-nearest prior SPY sessions by weekday + 1d/5d return + VIX."""
    spy = _read_daily("SPY")
    vix = _read_daily("VIX")
    fp = _build_fingerprint_df(spy, vix)
    if fp.empty:
        return []
    target = _today_fingerprint(live_tape)
    # Exclude today / incomplete last row for matching prior only
    fp = fp.iloc[:-1] if len(fp) > 1 else fp
    if fp.empty:
        return []

    def _dist(row: pd.Series) -> float:
        wd = 0.0 if float(row["weekday"]) == target["weekday"] else 2.5
        return (
            wd
            + abs(float(row["ret_1d"]) - target["ret_1d"]) * 0.35
            + abs(float(row["ret_5d"]) - target["ret_5d"]) * 0.15
            + abs(float(row["vix"]) - target["vix"]) * 0.08
        )

    fp = fp.copy()
    fp["dist"] = fp.apply(_dist, axis=1)
    fp = fp.nsmallest(max(1, k), "dist")
    out: list[dict[str, Any]] = []
    for _, row in fp.iterrows():
        d = pd.Timestamp(row["date"]).date()
        out.append(
            {
                "date": d.isoformat(),
                "weekday": _WD[d.weekday()],
                "spy_return_1d_pct": round(float(row["ret_1d"]), 3),
                "spy_return_5d_pct": round(float(row["ret_5d"]), 3),
                "vix": round(float(row["vix"]), 2),
                "distance": round(float(row["dist"]), 3),
            }
        )
    return out


def analogue_summary(analogues: list[dict[str, Any]]) -> str:
    if not analogues:
        return ""
    parts = [
        f"{a['date']}({a['weekday']}): SPY {a['spy_return_1d_pct']:+.2f}%/d"
        for a in analogues[:5]
    ]
    return "Analogue sessions: " + "; ".join(parts)
