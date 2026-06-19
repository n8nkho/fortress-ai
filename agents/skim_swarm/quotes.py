"""Live price and session change for skim universe (dashboard)."""
from __future__ import annotations

from typing import Any

from agents.skim_swarm.features import _fetch_bars
from agents.skim_swarm.observe import fetch_positions_map


def _session_last_and_change(sym: str, bars: dict) -> tuple[float | None, float | None]:
    df = bars.get(sym)
    if df is None or df.empty or "Close" not in df.columns:
        return None, None
    close = df["Close"].astype(float).dropna()
    if close.empty:
        return None, None
    last = float(close.iloc[-1])
    open_px = float(close.iloc[0])
    change_pct = round((last / open_px - 1) * 100, 3) if open_px > 0 else None
    return last, change_pct


def build_symbol_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Per-symbol last price, session % change, and open-position flags."""
    if not symbols:
        return {}
    _, _, positions = fetch_positions_map()
    bars = _fetch_bars(list(dict.fromkeys(symbols)))
    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        pos = positions.get(sym) or {}
        side = str(pos.get("side") or "flat")
        qty = int(pos.get("qty") or 0)
        is_open = side != "flat" and qty > 0

        last, change_pct = _session_last_and_change(sym, bars)
        cp = pos.get("current_price")
        if cp is not None:
            try:
                fv = float(cp)
                if fv > 0:
                    last = fv
            except (TypeError, ValueError):
                pass

        position_pct = None
        raw_ulpc = pos.get("unrealized_plpc")
        if raw_ulpc is not None and is_open:
            try:
                pv = float(raw_ulpc)
                position_pct = round(pv * 100 if abs(pv) <= 1.5 else pv, 3)
            except (TypeError, ValueError):
                position_pct = None

        avg_entry = pos.get("avg_entry_price")
        try:
            avg_entry = float(avg_entry) if avg_entry is not None else None
        except (TypeError, ValueError):
            avg_entry = None

        out[sym] = {
            "symbol": sym,
            "last": round(last, 4) if last is not None else None,
            "change_pct": change_pct,
            "position_pct": position_pct,
            "is_open": is_open,
            "side": side if is_open else "flat",
            "avg_entry": avg_entry,
            "unrealized_usd": round(float(pos.get("unrealized_pl") or 0), 4) if is_open else None,
        }
    return out
