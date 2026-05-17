"""Classic vs Fortress AI comparison metrics (realized P&L, chart series)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.classic_bridge import read_pnl_ledger_summary, resolve_classic_pnl_ledger_path
from utils.decision_log_metrics import extract_pnl_usd, win_rate_from_pnls


def _paper_starting_equity(env_key: str, default: float = 100_000.0) -> float:
    try:
        return float(os.environ.get(env_key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def ai_pnl_ledger_path(data_dir: Path) -> Path | None:
    for key in ("FORTRESS_AI_PNL_LEDGER_PATH", "FORTRESS_AI_EXTERNAL_LEDGER_PATH"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            p = Path(raw).expanduser()
            if p.is_file():
                return p
    local = data_dir / "pnl_ledger.jsonl"
    return local if local.is_file() else None


def ai_realized_from_decisions(data_dir: Path, *, max_rows: int = 5000) -> dict[str, Any]:
    path = data_dir / "ai_decisions.jsonl"
    pnls: list[float] = []
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_rows:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pnl = extract_pnl_usd(row)
                if pnl is not None:
                    pnls.append(float(pnl))
        except OSError:
            pass
    wins = sum(1 for p in pnls if p > 0)
    return {
        "count": len(pnls),
        "wins": wins,
        "losses": sum(1 for p in pnls if p < 0),
        "realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
        "win_rate": round(win_rate_from_pnls(pnls), 4) if pnls else None,
        "source": str(path) if pnls else None,
    }


def ai_realized_summary(data_dir: Path, portfolio: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Prefer AI pnl ledger, then decision-log PnL rows, then equity proxy (equity - start - unrealized).
    """
    ledger = ai_pnl_ledger_path(data_dir)
    if ledger:
        out = read_pnl_ledger_summary(ledger)
        if out.get("count"):
            return out

    dec = ai_realized_from_decisions(data_dir)
    if dec.get("count"):
        return dec

    port = portfolio or {}
    if port.get("connected") and port.get("equity") is not None:
        start = _paper_starting_equity("FORTRESS_AI_PAPER_STARTING_EQUITY", 100_000.0)
        equity = float(port["equity"])
        unreal = float(port.get("unrealized_pl") or 0)
        realized_proxy = round(equity - start - unreal, 2)
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl": realized_proxy,
            "win_rate": None,
            "source": "equity_proxy",
            "starting_equity": start,
        }
    return {
        "count": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl": None,
        "win_rate": None,
        "source": None,
    }


def classic_realized_summary() -> dict[str, Any]:
    return read_pnl_ledger_summary(resolve_classic_pnl_ledger_path())


def comparison_chart_series(
    *,
    classic_realized: float | None,
    classic_unrealized: float | None,
    ai_realized: float | None,
    ai_unrealized: float | None,
) -> dict[str, Any]:
    return {
        "labels": ["Classic", "Fortress AI"],
        "realized_usd": [
            round(float(classic_realized or 0), 2),
            round(float(ai_realized or 0), 2),
        ],
        "unrealized_usd": [
            round(float(classic_unrealized or 0), 2),
            round(float(ai_unrealized or 0), 2),
        ],
    }
