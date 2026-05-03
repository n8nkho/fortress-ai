"""
File-backed trading halt (kill switch). Honors FORTRESS_TRADING_HALT=1.

Optional FORTRESS_SHARED_HALT_PATH: same absolute path as Classic `data/operator_trading_halt.json`
so both dashboards can share one halt file without sharing other data.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SHARED = (os.environ.get("FORTRESS_SHARED_HALT_PATH") or "").strip()
HALT_PATH = Path(_SHARED) if _SHARED else (_ROOT / "data" / "operator_trading_halt.json")


def is_trading_halted() -> bool:
    if os.environ.get("FORTRESS_TRADING_HALT", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        if not HALT_PATH.exists():
            return False
        data = json.loads(HALT_PATH.read_text(encoding="utf-8"))
        return bool(data.get("active"))
    except Exception:
        return False


def get_halt_state() -> dict:
    env_on = os.environ.get("FORTRESS_TRADING_HALT", "").strip().lower() in ("1", "true", "yes", "on")
    file_state = {"active": False, "reason": "", "updated_at": None, "actor": ""}
    try:
        if HALT_PATH.exists():
            file_state.update(json.loads(HALT_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return {
        "env_halt": env_on,
        "file": file_state,
        "effective_halted": env_on or bool(file_state.get("active")),
    }


def set_trading_halt(active: bool, reason: str = "", actor: str = "fortress_ai_dashboard") -> dict:
    HALT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active": bool(active),
        "reason": (reason or "")[:500],
        "updated_at": datetime.now().isoformat(),
        "actor": actor[:120],
    }
    HALT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
