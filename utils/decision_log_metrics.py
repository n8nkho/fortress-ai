"""Extract PnL / trade stats from ai_decisions.jsonl rows for metrics and shadow tests."""
from __future__ import annotations

from typing import Any


def extract_pnl_usd(row: dict[str, Any]) -> float | None:
    """Best-effort realized PnL on a decision row (extend when sync writes fills)."""
    for key in ("pnl", "realized_pnl", "realized_pnl_usd", "fill_pnl", "closed_pnl_usd"):
        v = row.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    act = row.get("act")
    if isinstance(act, dict):
        d = act.get("detail")
        if isinstance(d, dict):
            for key in ("pnl", "realized_pnl", "pnl_usd", "closed_pnl"):
                v = d.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
    return None


def max_drawdown_fraction(pnls: list[float]) -> float | None:
    """Peak-to-trough drawdown on cumulative PnL curve (0–1 scale)."""
    if len(pnls) < 2:
        return None
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += float(p)
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def win_rate_from_pnls(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    wins = sum(1 for p in pnls if p > 0)
    return wins / len(pnls)


def trade_pnls_for_enter_executions(rows: list[dict[str, Any]]) -> list[float]:
    """PnL series for executed enter_position rows that carry a PnL figure."""
    out: list[float] = []
    for r in rows:
        d = r.get("decision")
        if not isinstance(d, dict):
            continue
        if (d.get("action") or "") != "enter_position":
            continue
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        if not act.get("executed"):
            continue
        pnl = extract_pnl_usd(r)
        if pnl is not None:
            out.append(float(pnl))
    return out


def trade_pnls_if_confidence_ge(rows: list[dict[str, Any]], threshold: float) -> list[float]:
    """Subset of trade PnLs that would execute under a confidence threshold (same logged trades)."""
    out: list[float] = []
    for r in rows:
        d = r.get("decision")
        if not isinstance(d, dict):
            continue
        if (d.get("action") or "") != "enter_position":
            continue
        try:
            c = float(d.get("confidence") or 0)
        except (TypeError, ValueError):
            continue
        if c < threshold:
            continue
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        if not act.get("executed"):
            continue
        pnl = extract_pnl_usd(r)
        if pnl is not None:
            out.append(float(pnl))
    return out
