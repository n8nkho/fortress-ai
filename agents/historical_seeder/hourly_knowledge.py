"""Build hourly slot statistics (weekday × hour ET) from downloaded bars."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from agents.historical_seeder.paths import hourly_dir, hourly_knowledge_path, prices_dir, repo_root

logger = logging.getLogger("historical_seeder.hourly_knowledge")

_ET = ZoneInfo("America/New_York")
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _read_hourly_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["ts"])
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["ts", "close"]).sort_values("ts")
    return df


def _slot_key(ts: pd.Timestamp) -> str | None:
    t = ts.tz_convert(_ET)
    if t.weekday() >= 5:
        return None
    hour = t.hour
    minute = t.minute
    if hour < 9 or (hour == 9 and minute < 30):
        return None
    if hour >= 16:
        return None
    return f"{_WD[t.weekday()]}-{hour:02d}"


def _hour_weight_profile(df: pd.DataFrame) -> dict[str, float]:
    """Share of |return| by weekday-hour slot from true hourly bars."""
    if df.empty or len(df) < 2:
        return {}
    work = df.copy()
    work["ret_pct"] = work["close"].pct_change().abs() * 100.0
    work = work.iloc[1:]
    totals: dict[str, float] = {}
    for _, row in work.iterrows():
        key = _slot_key(row["ts"])
        if key:
            totals[key] = totals.get(key, 0.0) + float(row["ret_pct"])
    grand = sum(totals.values()) or 1.0
    return {k: v / grand for k, v in totals.items()}


def _read_daily_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["date", "close"]).sort_values("date")


def _years_cutoff() -> pd.Timestamp:
    yrs = int(os.environ.get("FORTRESS_HOURLY_KNOWLEDGE_YEARS", "5") or 5)
    return pd.Timestamp(datetime.now(_ET) - pd.Timedelta(days=int(365.25 * yrs)))


def _synthetic_slot_returns_from_daily(
    daily_df: pd.DataFrame,
    weights: dict[str, float],
    *,
    before: pd.Timestamp | None,
) -> dict[str, list[float]]:
    """Approximate hourly slot returns from daily bars (pre-hourly era)."""
    if daily_df.empty or len(daily_df) < 2:
        return {}
    work = daily_df.copy()
    work["ret_pct"] = work["close"].pct_change() * 100.0
    out: dict[str, list[float]] = {}
    for _, row in work.iloc[1:].iterrows():
        d = pd.Timestamp(row["date"])
        if d.tzinfo is None:
            d = d.tz_localize(_ET)
        else:
            d = d.tz_convert(_ET)
        if before is not None and d.normalize() >= before.tz_convert(_ET).normalize():
            continue
        if d.weekday() >= 5:
            continue
        if d < _years_cutoff():
            continue
        wd = _WD[d.weekday()]
        day_ret = float(row["ret_pct"])
        day_slots = [f"{wd}-{h:02d}" for h in range(9, 16)]
        raw_w = [weights.get(s, 1.0 / len(day_slots)) for s in day_slots]
        w_sum = sum(raw_w) or 1.0
        for sk, w in zip(day_slots, raw_w):
            out.setdefault(sk, []).append(day_ret * (w / w_sum))
    return out


def _vix_regime(vix: float) -> str:
    if vix < 18.0:
        return "low"
    if vix <= 25.0:
        return "mid"
    return "high"


def _load_vix_by_date() -> dict[str, float]:
    df = _read_daily_csv(prices_dir() / "VIX_daily.csv")
    if df.empty:
        return {}
    out: dict[str, float] = {}
    for _, row in df.iterrows():
        d = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        out[d] = float(row["close"])
    return out


def _date_et_from_ts(ts: pd.Timestamp) -> str:
    return ts.tz_convert(_ET).strftime("%Y-%m-%d")


def _aggregate_slot_stats(slots: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, rets in slots.items():
        if len(rets) < 8:
            continue
        s = pd.Series(rets)
        out[key] = {
            "mean_return_pct": round(float(s.mean()), 4),
            "std_return_pct": round(float(s.std()), 4),
            "median_return_pct": round(float(s.median()), 4),
            "win_rate_long": round(float((s > 0).mean()), 4),
            "sample_count": int(len(rets)),
        }
    return out


def build_symbol_slot_stats(
    df: pd.DataFrame,
    *,
    extra_slot_returns: dict[str, list[float]] | None = None,
    vix_by_date: dict[str, float] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    if df.empty or len(df) < 2:
        empty_reg: dict[str, dict[str, dict[str, Any]]] = {"low": {}, "mid": {}, "high": {}}
        return {}, empty_reg
    df = df.copy()
    df["ret_pct"] = df["close"].pct_change() * 100.0
    df = df.iloc[1:]
    slots: dict[str, list[float]] = {}
    reg_slots: dict[str, dict[str, list[float]]] = {"low": {}, "mid": {}, "high": {}}
    vix_map = vix_by_date or {}

    def _append(key: str, ret: float, day: str) -> None:
        slots.setdefault(key, []).append(ret)
        vix = vix_map.get(day, 20.0)
        reg = _vix_regime(vix)
        reg_slots[reg].setdefault(key, []).append(ret)

    for _, row in df.iterrows():
        key = _slot_key(row["ts"])
        if not key:
            continue
        day = _date_et_from_ts(row["ts"])
        _append(key, float(row["ret_pct"]), day)

    if extra_slot_returns:
        for key, rets in extra_slot_returns.items():
            slots.setdefault(key, []).extend(rets)
        for key, rets in (extra_slot_returns or {}).items():
            wd = key.split("-")[0] if "-" in key else ""
            for ret in rets:
                reg_slots["mid"].setdefault(key, []).append(ret)

    regime_out = {reg: _aggregate_slot_stats(rs) for reg, rs in reg_slots.items()}
    return _aggregate_slot_stats(slots), regime_out


def build_hourly_knowledge(
    *,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    hourly_dir().mkdir(parents=True, exist_ok=True)
    sym_list = symbols or [p.stem.replace("_hourly", "") for p in sorted(hourly_dir().glob("*_hourly.csv"))]
    slot_map: dict[str, dict[str, dict[str, Any]]] = {}
    regime_map: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    row_counts: dict[str, int] = {}
    vix_by_date = _load_vix_by_date()
    for sym in sym_list:
        path = hourly_dir() / f"{sym}_hourly.csv"
        df = _read_hourly_csv(path)
        row_counts[sym] = len(df)
        daily_path = prices_dir() / f"{sym}_daily.csv"
        daily_df = _read_daily_csv(daily_path)
        weights = _hour_weight_profile(df)
        before = df["ts"].min() if not df.empty else None
        extra = _synthetic_slot_returns_from_daily(daily_df, weights, before=before)
        stats, reg_stats = build_symbol_slot_stats(df, extra_slot_returns=extra, vix_by_date=vix_by_date)
        if stats:
            slot_map[sym] = stats
            regime_map[sym] = reg_stats

    doc: dict[str, Any] = {
        "version": 1,
        "built_at": datetime.now(_ET).isoformat(),
        "system_tz": "America/New_York",
        "years": int(os.environ.get("FORTRESS_HOURLY_KNOWLEDGE_YEARS", "5") or 5),
        "hourly_window_days": 729,
        "daily_extension": True,
        "regime_vix_bands": {"low": "<18", "mid": "18-25", "high": ">25"},
        "description": "5-year RTH slot profile: true hourly (~2y) + daily-synthesized prior years; VIX regimes",
        "symbols": list(slot_map.keys()),
        "row_counts": row_counts,
        "slots": slot_map,
        "slots_regime": regime_map,
    }
    return doc


def write_hourly_knowledge(doc: dict[str, Any] | None = None) -> Path:
    doc = doc if doc is not None else build_hourly_knowledge()
    p = hourly_knowledge_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    logger.info("wrote hourly knowledge %s symbols", len(doc.get("symbols") or []))
    return p


def run_build(*, download: bool = True, force_download: bool = False) -> dict[str, Any]:
    if download:
        from agents.historical_seeder.hourly_loader import run_hourly_downloads

        dl = run_hourly_downloads(force=force_download)
    else:
        dl = {"skipped": True}
    doc = build_hourly_knowledge()
    path = write_hourly_knowledge(doc)
    return {
        "download": dl,
        "knowledge_path": str(path.relative_to(repo_root())),
        "symbols": doc.get("symbols"),
        "slot_counts": {s: len(v) for s, v in (doc.get("slots") or {}).items()},
    }
