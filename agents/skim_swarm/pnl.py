"""Skim swarm realized / unrealized P&L summaries for dashboard and monitoring."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agents.skim_swarm.eod import session_date_et
from agents.skim_swarm.observe import observe_account
from agents.skim_swarm.state import load_swarm_state
from utils.skim_swarm_config import swarm_data_dir, universe
from utils.swarm_decisions_pnl import (
    cumulative_realized_from_decisions,
    daily_realized_from_decisions,
)

_ET = ZoneInfo("America/New_York")


def _wave_session_date(ts_raw: str) -> str | None:
    from utils.swarm_decisions_pnl import wave_session_date_et

    return wave_session_date_et(ts_raw)


def session_daily_realized_usd(session: str | None = None) -> float:
    """Authoritative session realized P&L from executed exits in decisions.jsonl."""
    sess = session or session_date_et()
    total, _ = _daily_realized_from_decisions(sess)
    return total


def _learned_session_stats(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("session_stats") or data.get("stats") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def learned_symbol_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Dashboard-friendly learned stats + params (v4 session_stats aware)."""
    stats = _learned_session_stats(data)
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    exits = int(stats.get("exits") or 0)
    pnl = float(stats.get("sum_pnl_usd") or 0)
    realized = round(pnl, 4) if exits > 0 or pnl != 0 else None
    return {
        "stats": stats,
        "params": params,
        "target_mult": params.get("target_mult", data.get("target_mult")),
        "enter_long_delta": params.get("enter_long_delta", data.get("enter_long_delta")),
        "enter_short_delta": params.get("enter_short_delta", data.get("enter_short_delta")),
        "realized_usd": realized,
        "wins": int(stats.get("wins") or 0),
        "losses": int(stats.get("losses") or 0),
        "exits": exits,
    }


def _daily_realized_from_decisions(session: str) -> tuple[float, int]:
    return daily_realized_from_decisions(swarm_data_dir() / "decisions.jsonl", session)


def _session_realized_from_learned() -> tuple[float, list[dict[str, Any]]]:
    """Per-symbol realized for current session (learned stats reset daily)."""
    learned_dir = swarm_data_dir() / "learned"
    total = 0.0
    rows: list[dict[str, Any]] = []
    if not learned_dir.is_dir():
        return 0.0, rows
    session = session_date_et()
    for f in sorted(learned_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("session_date_et") not in (None, session):
            continue
        sym = data.get("symbol") or f.stem.replace("_", ".")
        stats = _learned_session_stats(data)
        pnl = float(stats.get("sum_pnl_usd") or 0)
        exits = int(stats.get("exits") or 0)
        if exits == 0 and pnl == 0:
            continue
        total += pnl
        rows.append(
            {
                "symbol": sym,
                "realized_usd": round(pnl, 4),
                "exits": exits,
                "wins": int(stats.get("wins") or 0),
                "losses": int(stats.get("losses") or 0),
            }
        )
    return round(total, 4), sorted(rows, key=lambda r: r["realized_usd"])


def _open_unrealized() -> tuple[float, int, list[dict[str, Any]]]:
    acct = observe_account()
    positions = acct.get("positions") or {}
    total = 0.0
    count = 0
    detail: list[dict[str, Any]] = []
    for sym in universe():
        p = positions.get(sym) or {}
        side = p.get("side") or "flat"
        qty = int(p.get("qty") or 0)
        if side == "flat" or qty <= 0:
            continue
        u = float(p.get("unrealized_pl") or 0)
        total += u
        count += 1
        detail.append(
            {
                "symbol": sym,
                "side": side,
                "qty": qty,
                "unrealized_usd": round(u, 4),
            }
        )
    return round(total, 4), count, sorted(detail, key=lambda r: r["unrealized_usd"])


def compute_pnl_summary() -> dict[str, Any]:
    session = session_date_et()
    dec_path = swarm_data_dir() / "decisions.jsonl"
    daily_realized, daily_exits = _daily_realized_from_decisions(session)
    cumulative_realized, cumulative_exits = cumulative_realized_from_decisions(dec_path)
    session_realized, per_symbol = _session_realized_from_learned()
    open_unreal, open_count, open_detail = _open_unrealized()
    swarm = load_swarm_state()
    swarm_daily = round(float(swarm.get("day_realized_pnl") or 0), 4)

    daily_net = round(daily_realized + open_unreal, 4)
    cumulative_net = round(cumulative_realized + open_unreal, 4)

    return {
        "session_date_et": session,
        "daily": {
            "realized_usd": daily_realized,
            "unrealized_usd": open_unreal,
            "net_usd": daily_net,
            "exit_count": daily_exits,
            "open_positions": open_count,
        },
        "cumulative": {
            "realized_usd": cumulative_realized,
            "unrealized_usd": open_unreal,
            "net_usd": cumulative_net,
            "exit_count": cumulative_exits,
            "open_positions": open_count,
        },
        "session_learned_realized_usd": session_realized,
        "swarm_state_daily_realized_usd": swarm_daily,
        "swarm_halted": bool(swarm.get("halted")),
        "halt_reason": swarm.get("halt_reason"),
        "open_positions_detail": open_detail,
        "per_symbol_realized": per_symbol,
        "updated_utc": datetime.now(_ET).astimezone(ZoneInfo("UTC")).isoformat(),
    }
