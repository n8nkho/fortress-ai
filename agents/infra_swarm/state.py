"""Per-symbol and swarm-level persisted state."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.infra_swarm.eod import session_date_et
from utils.infra_swarm_config import swarm_data_dir


def _state_dir() -> Path:
    d = swarm_data_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _swarm_path() -> Path:
    return swarm_data_dir() / "swarm_state.json"


def load_swarm_state() -> dict[str, Any]:
    p = _swarm_path()
    if not p.exists():
        return {"session_date_et": session_date_et(), "day_realized_pnl": 0.0, "halted": False}
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("session_date_et") != session_date_et():
        return {"session_date_et": session_date_et(), "day_realized_pnl": 0.0, "halted": False, "halt_reason": None}
    from agents.infra_swarm.pnl import session_daily_realized_usd

    st["day_realized_pnl"] = round(session_daily_realized_usd(), 4)
    return st


def save_swarm_state(st: dict[str, Any]) -> None:
    st["session_date_et"] = session_date_et()
    st["updated_utc"] = datetime.now(timezone.utc).isoformat()
    _swarm_path().write_text(json.dumps(st, indent=2), encoding="utf-8")


def load_symbol_state(symbol: str) -> dict[str, Any]:
    sym = symbol.upper()
    p = _state_dir() / f"{sym.replace('.', '_')}.json"
    if not p.exists():
        return {
            "symbol": sym,
            "session_date_et": session_date_et(),
            "side": "flat",
            "entry_price": None,
            "entry_ts": None,
            "peak_unrealized": 0.0,
            "cooldown_until_utc": None,
            "last_action": "wait",
            "last_block_reason": None,
        }
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("session_date_et") != session_date_et():
        return {
            "symbol": sym,
            "session_date_et": session_date_et(),
            "side": "flat",
            "entry_price": None,
            "entry_ts": None,
            "peak_unrealized": 0.0,
            "cooldown_until_utc": None,
            "last_action": "wait",
            "last_block_reason": None,
        }
    return st


def save_symbol_state(st: dict[str, Any]) -> None:
    sym = str(st.get("symbol") or "UNK").upper()
    st["session_date_et"] = session_date_et()
    st["updated_utc"] = datetime.now(timezone.utc).isoformat()
    p = _state_dir() / f"{sym.replace('.', '_')}.json"
    p.write_text(json.dumps(st, indent=2), encoding="utf-8")
