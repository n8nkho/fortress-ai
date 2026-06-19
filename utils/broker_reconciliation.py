"""Broker vs ledger vs operator reconciliation — catches silent execution drift."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_LEDGER = _ROOT / "data" / "pnl_ledger.jsonl"
_OPERATOR = _ROOT / "data" / "operator_status" / "latest.json"


def _read_json(path: Path, default: dict | None = None) -> dict[str, Any]:
    if not path.is_file():
        return default or {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else (default or {})
    except Exception:
        return default or {}


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def scan_premature_exit_ledger(*, max_age_hours: float = 72.0) -> list[dict[str, Any]]:
    """Flag ledger exits recorded before broker fill (legacy exit_fill_pnl_estimate)."""
    if not _LEDGER.is_file():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    hits: list[dict[str, Any]] = []
    for line in _LEDGER.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        note = str((row.get("note") or (row.get("extra") or {}).get("note") or "")).lower()
        if note != "exit_fill_pnl_estimate":
            continue
        ts = _parse_ts(row.get("timestamp") or row.get("ts"))
        if ts and ts < cutoff:
            continue
        hits.append(row)
    if not hits:
        return []
    syms = sorted({str(r.get("symbol") or r.get("ticker") or "") for r in hits if r.get("symbol") or r.get("ticker")})
    return [
        {
            "code": "premature_exit_ledger",
            "severity": "high",
            "component": "unified_ai_agent",
            "count": len(hits),
            "symbols": syms[:12],
            "recommendation": (
                "PnL ledger recorded exits before Alpaca fill — reconcile broker positions; "
                "use exit_fill_confirmed marker only."
            ),
            "si_action": "reconcile_broker_ledger",
            "mitigation_markers": ["exit_fill_confirmed", "exit_unfilled"],
        }
    ]


def scan_operator_broker_open_drift() -> list[dict[str, Any]]:
    """Operator report skim count vs broker_open_positions when both present."""
    doc = _read_json(_OPERATOR, default={})
    if not isinstance(doc, dict):
        return []
    port = doc.get("portfolio") or {}
    broker = int(port.get("broker_open_positions") or 0)
    skim = int(port.get("skim_open_positions") or port.get("open_positions") or 0)
    if broker <= 0:
        return []
    if broker == skim:
        return []
    return [
        {
            "code": "operator_broker_open_drift",
            "severity": "medium" if broker <= skim + 2 else "high",
            "component": "operator_status",
            "broker_open_positions": broker,
            "skim_open_positions": skim,
            "broker_symbols": port.get("broker_symbols") or [],
            "recommendation": (
                "Open position count from Alpaca exceeds swarm-reported open — "
                "legacy/orphan holdings or ledger drift; reconcile exits."
            ),
            "si_action": "reconcile_broker_ledger",
        }
    ]


def scan_broker_open_orders_backlog(*, max_open_sells: int = 25) -> list[dict[str, Any]]:
    """Live Alpaca check — too many open SELL orders indicates exit spam."""
    if str(os.environ.get("FORTRESS_BROKER_RECON_LIVE", "1")).strip().lower() in ("0", "false", "no"):
        return []
    try:
        from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs

        key, sec = alpaca_credentials()
        if not key or not sec:
            return []
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
        orders = list(
            tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)) or []
        )
    except Exception:
        return []

    sells = [
        o
        for o in orders
        if str(getattr(getattr(o, "side", ""), "value", getattr(o, "side", ""))).lower() == "sell"
    ]
    if len(sells) <= max_open_sells:
        return []
    phantom = 0
    held: set[str] = set()
    try:
        for p in tc.get_all_positions():
            sym = str(getattr(p, "symbol", "")).upper()
            if sym and float(getattr(p, "qty", 0) or 0) != 0:
                held.add(sym)
        for o in sells:
            sym = str(getattr(o, "symbol", "")).upper()
            if sym and sym not in held:
                phantom += 1
    except Exception:
        phantom = 0

    return [
        {
            "code": "broker_open_sell_backlog",
            "severity": "critical" if len(sells) > 100 else "high",
            "component": "alpaca_execution",
            "open_sell_count": len(sells),
            "phantom_sell_count": phantom,
            "held_symbols": sorted(held)[:20],
            "recommendation": (
                "Cancel stale open SELL orders and enable open_exit_order_pending gate "
                "before submitting new exits."
            ),
            "si_action": "cancel_stale_open_orders",
            "mitigation_markers": ["open_exit_order_pending"],
        }
    ]


def scan_broker_reconciliation() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.extend(scan_premature_exit_ledger())
    out.extend(scan_operator_broker_open_drift())
    out.extend(scan_broker_open_orders_backlog())
    return out
