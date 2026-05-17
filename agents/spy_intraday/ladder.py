"""Entry/exit ladder state for scaled intraday index trades."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from utils.spy_agent_config import ladder_rungs, rung_notional_usd, spy_data_dir


def ladder_state_path() -> Any:
    return spy_data_dir() / "ladder_state.json"


def load_ladder_state() -> dict[str, Any]:
    p = ladder_state_path()
    if not p.exists():
        return _empty_state()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else _empty_state()
    except Exception:
        return _empty_state()


def _empty_state() -> dict[str, Any]:
    return {
        "side": "flat",
        "rungs_open": 0,
        "max_rungs": ladder_rungs(),
        "rung_notional_usd": rung_notional_usd(),
        "session_date_et": None,
        "entries": [],
    }


def save_ladder_state(state: dict[str, Any]) -> None:
    p = ladder_state_path()
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_session_if_new_day(state: dict[str, Any], session_date_et: str) -> dict[str, Any]:
    if state.get("session_date_et") != session_date_et:
        fresh = _empty_state()
        fresh["session_date_et"] = session_date_et
        return fresh
    state["max_rungs"] = ladder_rungs()
    state["rung_notional_usd"] = rung_notional_usd()
    return state


def shares_for_rung(price: float) -> int:
    if price <= 0:
        return 0
    return max(1, int(rung_notional_usd() // price))


def can_add_rung(state: dict[str, Any], side: str) -> bool:
    side = side.lower()
    cur = (state.get("side") or "flat").lower()
    rungs = int(state.get("rungs_open") or 0)
    max_r = int(state.get("max_rungs") or ladder_rungs())
    if rungs >= max_r:
        return False
    if cur == "flat":
        return True
    return cur == side


def record_rung(state: dict[str, Any], *, side: str, qty: int, price: float, action: str) -> dict[str, Any]:
    side = side.lower()
    entries = list(state.get("entries") or [])
    entries.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "side": side,
            "qty": qty,
            "price": price,
        }
    )
    state["entries"] = entries[-20:]
    if action in ("add_long", "add_short"):
        if (state.get("side") or "flat") == "flat":
            state["side"] = side
        state["rungs_open"] = int(state.get("rungs_open") or 0) + 1
    elif action in ("trim", "flatten"):
        state["rungs_open"] = max(0, int(state.get("rungs_open") or 0) - 1)
        if state["rungs_open"] == 0:
            state["side"] = "flat"
    elif action == "flatten_all":
        state["rungs_open"] = 0
        state["side"] = "flat"
    return state
