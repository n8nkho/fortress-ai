"""Detect and reconcile premature exit ledger entries vs broker state."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.unified_ai_agent.exit_handler import EXIT_FILL_CONFIRMED, EXIT_UNFILLED

_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _ledger_path() -> Path:
    from utils.ai_pnl_ledger import ledger_path

    return ledger_path()


def _fetch_broker_symbols() -> set[str]:
    try:
        from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs

        key, sec = alpaca_credentials()
        if not key or not sec:
            return set()
        from alpaca.trading.client import TradingClient

        tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
        held: set[str] = set()
        for p in tc.get_all_positions():
            sym = str(getattr(p, "symbol", "") or "").upper()
            if sym and float(getattr(p, "qty", 0) or 0) != 0:
                held.add(sym)
        return held
    except Exception:
        return set()


def _row_fill_status(row: dict[str, Any]) -> str:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return str(
        row.get("fill_status")
        or (extra or {}).get("fill_status")
        or row.get("note")
        or (extra or {}).get("note")
        or ""
    ).strip()


def scan_unconfirmed_ledger_entries(
    *,
    max_age_hours: float = 72.0,
    ledger_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return exit rows missing exit_fill_confirmed (incl. legacy exit_fill_pnl_estimate)."""
    p = ledger_path or _ledger_path()
    if not p.is_file():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    hits: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = _row_fill_status(row).lower()
        if status == EXIT_FILL_CONFIRMED:
            continue
        if status not in ("exit_fill_pnl_estimate", "") and status != EXIT_UNFILLED:
            if str(row.get("source") or "") != "unified_ai_agent":
                continue
        ts = _parse_ts(row.get("timestamp") or row.get("ts"))
        if ts and ts < cutoff:
            continue
        if status in ("exit_fill_pnl_estimate", EXIT_UNFILLED, "") or str(
            row.get("action") or (row.get("extra") or {}).get("action") or ""
        ) == "exit_position":
            hits.append(row)
    return hits


def reconcile_premature_exits(
    *,
    broker_symbols: set[str] | None = None,
    max_age_hours: float = 72.0,
) -> dict[str, Any]:
    """Report premature ledger rows; flag symbols still open at broker."""
    entries = scan_unconfirmed_ledger_entries(max_age_hours=max_age_hours)
    if broker_symbols is None:
        broker_symbols = _fetch_broker_symbols()

    corrections: list[dict[str, Any]] = []
    for row in entries:
        sym = str(row.get("symbol") or row.get("ticker") or "").upper()
        corrections.append(
            {
                "symbol": sym,
                "order_id": row.get("order_id"),
                "fill_status": _row_fill_status(row) or EXIT_UNFILLED,
                "still_held_at_broker": bool(sym and sym in broker_symbols),
                "marker": "premature_exit_ledger",
            }
        )

    return {
        "ok": len(entries) == 0,
        "count": len(entries),
        "symbols": sorted({c["symbol"] for c in corrections if c.get("symbol")})[:12],
        "corrections": corrections,
        "mitigation_markers": ["exit_fill_confirmed", "exit_unfilled", "premature_exit_ledger"],
        "si_action": "reconcile_broker_ledger",
    }


def backfill_legacy_exit_ledger(*, ledger_path: Path | None = None) -> dict[str, Any]:
    """
    Repair legacy exit_fill_pnl_estimate rows via Alpaca order lookup.
    Confirmed fills -> exit_fill_confirmed; no fill -> unconfirmed_legacy (excluded from PnL).
    """
    from agents.unified_ai_agent.exit_handler import EXIT_FILL_CONFIRMED
    from utils.alpaca_order_confirm import poll_order_fill

    p = ledger_path or _ledger_path()
    if not p.is_file():
        return {"ok": True, "skipped": "no_ledger", "confirmed": 0, "flagged": 0}

    tc = None
    try:
        from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs
        from alpaca.trading.client import TradingClient

        key, sec = alpaca_credentials()
        if key and sec:
            tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
    except Exception as e:
        return {"ok": False, "error": f"alpaca_client:{e}"}

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    confirmed = 0
    flagged = 0
    skipped = 0
    details: list[dict[str, Any]] = []
    out_lines: list[str] = []

    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            out_lines.append(raw)
            continue
        note = _row_fill_status(row).lower()
        if note != "exit_fill_pnl_estimate":
            out_lines.append(raw)
            continue

        oid = str(row.get("order_id") or "").strip()
        sym = str(row.get("symbol") or row.get("ticker") or "").upper()
        updated = dict(row)
        if not oid or oid == "oid" or not tc:
            updated["note"] = "unconfirmed_legacy"
            updated["fill_status"] = "unconfirmed_legacy"
            updated["exclude_from_pnl"] = True
            flagged += 1
            details.append({"symbol": sym, "order_id": oid, "action": "flagged_unconfirmed_legacy"})
        else:
            try:
                order = tc.get_order_by_id(oid)
                fq = int(float(getattr(order, "filled_qty", 0) or 0))
            except Exception:
                poll = poll_order_fill(tc, oid, timeout_sec=2.0, poll_interval_sec=0.5)
                fq = int(poll.get("filled_qty") or 0)
            if fq > 0:
                updated["note"] = EXIT_FILL_CONFIRMED
                updated["fill_status"] = EXIT_FILL_CONFIRMED
                updated["filled_qty"] = fq
                confirmed += 1
                details.append({"symbol": sym, "order_id": oid, "action": "confirmed", "filled_qty": fq})
            else:
                updated["note"] = "unconfirmed_legacy"
                updated["fill_status"] = "unconfirmed_legacy"
                updated["exclude_from_pnl"] = True
                flagged += 1
                details.append({"symbol": sym, "order_id": oid, "action": "flagged_unconfirmed_legacy"})
        out_lines.append(json.dumps(updated, default=str))

    p.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    return {
        "ok": True,
        "confirmed": confirmed,
        "flagged": flagged,
        "skipped": skipped,
        "details": details[:30],
    }
