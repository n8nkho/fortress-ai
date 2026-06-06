"""Realized P&L from swarm decisions.jsonl (authoritative exit fills)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def wave_session_date_et(ts_raw: str) -> str | None:
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        return ts.astimezone(_ET).date().isoformat()
    except Exception:
        return None


def iter_executed_exits(decisions_path: Path) -> list[dict[str, Any]]:
    """Yield exit rows: session_date_et, symbol, pnl_usd, ts."""
    if not decisions_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            wave = json.loads(line)
        except json.JSONDecodeError:
            continue
        session = wave_session_date_et(str(wave.get("ts") or ""))
        if not session:
            continue
        for row in wave.get("results") or []:
            act = row.get("act") or {}
            dec = row.get("decision") or {}
            if not act.get("executed"):
                continue
            if dec.get("action") not in ("exit_position", "flatten", "exit_partial"):
                continue
            u = (row.get("features") or {}).get("unrealized_usd")
            if u is None:
                continue
            pnl = float(u)
            if dec.get("action") == "exit_partial":
                exit_qty = max(1, int(dec.get("exit_qty") or 1))
                pos_qty = max(1, int((row.get("features") or {}).get("position_qty") or exit_qty))
                pnl = pnl / pos_qty * exit_qty
            rows.append(
                {
                    "session_date_et": session,
                    "symbol": str(row.get("symbol") or dec.get("symbol") or ""),
                    "pnl_usd": round(pnl, 4),
                    "ts": str(row.get("ts") or wave.get("ts") or ""),
                }
            )
    return rows


def daily_realized_from_decisions(decisions_path: Path, session: str) -> tuple[float, int]:
    total = 0.0
    exits = 0
    for row in iter_executed_exits(decisions_path):
        if row["session_date_et"] != session:
            continue
        total += float(row["pnl_usd"])
        exits += 1
    return round(total, 4), exits


def cumulative_realized_from_decisions(decisions_path: Path) -> tuple[float, int]:
    total = 0.0
    exits = 0
    for row in iter_executed_exits(decisions_path):
        total += float(row["pnl_usd"])
        exits += 1
    return round(total, 4), exits


def per_session_totals(decisions_path: Path) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for row in iter_executed_exits(decisions_path):
        day = row["session_date_et"]
        bucket = by.setdefault(day, {"realized_usd": 0.0, "exit_count": 0})
        bucket["realized_usd"] = round(float(bucket["realized_usd"]) + float(row["pnl_usd"]), 4)
        bucket["exit_count"] = int(bucket["exit_count"]) + 1
    return by
