"""Fortress AI paper account realized P&L ledger (for SI shadow tests and dashboard)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def ledger_path() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PNL_LEDGER_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    root = Path(__file__).resolve().parent.parent
    dd = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    base = Path(dd) if dd else (root / "data")
    return base / "pnl_ledger.jsonl"


def append_realized_fill(
    *,
    symbol: str,
    pnl_usd: float,
    side: str,
    qty: int,
    order_id: str | None = None,
    source: str = "unified_ai_agent",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a closed-trade or exit fill for SI / diagnostics."""
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": symbol.upper()[:12],
        "symbol": symbol.upper()[:12],
        "pnl": round(float(pnl_usd), 4),
        "side": side,
        "qty": int(qty),
        "order_id": order_id,
        "source": source,
        **(extra or {}),
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def summarize_ledger(*, max_rows: int = 5000) -> dict[str, Any]:
    p = ledger_path()
    total = 0.0
    n = 0
    wins = 0
    if not p.is_file():
        return {"count": 0, "realized_pnl": 0.0, "win_rate": None, "source": None}
    try:
        for line in p.read_text(encoding="utf-8").splitlines()[-max_rows:]:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                pv = float(o.get("pnl"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            total += pv
            n += 1
            if pv > 0:
                wins += 1
    except OSError:
        pass
    return {
        "count": n,
        "realized_pnl": round(total, 2),
        "win_rate": round(wins / n, 4) if n else None,
        "source": str(p),
    }
