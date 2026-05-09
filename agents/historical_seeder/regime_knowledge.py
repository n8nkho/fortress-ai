"""Regime duration, transitions, and playbook-style belief stubs."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from agents.historical_seeder.paths import features_dir

logger = logging.getLogger("historical_seeder.regime_knowledge")


def _read_spy_regime() -> pd.DataFrame:
    p = features_dir() / "SPY_features.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def mine_regime_stats() -> dict[str, Any]:
    df = _read_spy_regime()
    if df.empty or "regime" not in df.columns:
        return {"error": "missing SPY_features"}
    df = df.copy()
    df["prev_regime"] = df["regime"].shift(1)
    df.loc[0, "prev_regime"] = df.loc[0, "regime"]

    durations: dict[str, list[int]] = defaultdict(list)
    returns_by_regime: dict[str, list[float]] = defaultdict(list)
    c = df["adj_close"].fillna(df["close"])
    df["ret"] = c.pct_change() * 100.0
    df["block"] = (df["regime"] != df["regime"].shift(1)).cumsum()
    for _, seg in df.groupby("block"):
        reg = str(seg["regime"].iloc[0])
        durations[reg].append(len(seg))
        returns_by_regime[reg].append(float(seg["ret"].sum()))

    transitions: dict[tuple[str, str], int] = defaultdict(int)
    for i in range(1, len(df)):
        a, b = str(df.loc[i - 1, "regime"]), str(df.loc[i, "regime"])
        if a != b:
            transitions[(a, b)] += 1

    avg_dur = {k: float(np.mean(v)) for k, v in durations.items()}
    avg_ret = {k: float(np.mean(v)) if v else 0.0 for k, v in returns_by_regime.items()}

    # Simple trigger heuristic counts
    bear_days = df[(df["regime"] == "BEAR")]
    high_vol_days = df[df["regime"] == "HIGH_VOL"]

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "avg_duration_trading_days": avg_dur,
        "avg_cumulative_daily_return_pct_per_stint": avg_ret,
        "transition_counts_since_series_start": {f"{a}->{b}": n for (a, b), n in sorted(transitions.items())},
        "bear_days_total": int(len(bear_days)),
        "high_vol_days_total": int(len(high_vol_days)),
    }


def build_playbook_summaries(pattern_results: dict[str, Any]) -> dict[str, str]:
    """Short text playbooks from mined patterns + regime stats."""
    pats = pattern_results.get("patterns") or {}
    lines_bear = []
    lines_high = []
    lines_bull = []
    mr = pats.get("mean_reversion_rsi_extremes") or {}
    if mr.get("total_occurrences"):
        lines_bull.append(
            f"RSI mean-reversion samples={mr.get('total_occurrences')} win_rate={mr.get('win_rate', 0):.2f}"
        )
    pv = pats.get("mean_reversion_high_vol_vs_calm") or {}
    if pv:
        lines_high.append(f"MR after RSI<30: high-VIX avg 5d fwd {pv.get('high_vix_gt25', {}).get('avg_fwd5_pct')}")
        lines_bull.append(f"MR after RSI<30: calm-VIX avg 5d fwd {pv.get('low_vix_lt20', {}).get('avg_fwd5_pct')}")
    dd = pats.get("post_drawdown_recovery") or {}
    if dd.get("win_rate") is not None:
        lines_bear.append(f"After 20d drawdown >10%: 60d win_rate={dd.get('win_rate', 0):.2f}")

    return {
        "BEAR_MARKET_PLAYBOOK": "; ".join(lines_bear) or "Avoid heroic catches until volatility stabilizes; favour risk reduction.",
        "HIGH_VOL_PLAYBOOK": "; ".join(lines_high) or "Reduce size; prioritize survival over tactical MR.",
        "BULL_MARKET_PLAYBOOK": "; ".join(lines_bull) or "Trend and dip-buying historically fair better when VIX <20 and SPY > MA200.",
    }


def regime_transition_beliefs(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthetic META beliefs one per transition type."""
    rows = []
    now = datetime.utcnow().isoformat() + "Z"
    tc = stats.get("transition_counts_since_series_start") or {}
    for label, cnt in tc.items():
        if cnt < 5:
            continue
        rows.append(
            {
                "belief_type": "REGIME_KNOWLEDGE",
                "transition": label,
                "occurrences": int(cnt),
                "created_at": now,
                "summary": f"Observed {cnt} transitions of type {label} since 2000 in SPY/VIX regime series.",
            }
        )
    return rows
