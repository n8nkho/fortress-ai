"""Unified AI enter dedup — block re-entry when observation is stale vs broker."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.system_time import parse_iso


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def state_path() -> Path:
    return _data_dir() / "unified_ai" / "enter_guard.json"


def enter_cooldown_sec() -> int:
    try:
        return max(60, int(os.environ.get("FORTRESS_UNIFIED_ENTER_COOLDOWN_SEC", "900") or 900))
    except ValueError:
        return 900


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.exists():
        return {"symbols": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"symbols": {}}
    except Exception:
        return {"symbols": {}}


def save_state(doc: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_utc"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _sym_key(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def entry_blocked_by_cooldown(symbol: str, *, held_qty: int) -> tuple[bool, str | None]:
    """
    Block enter when broker shows flat but we recently entered (observation lag).

    Returns (blocked, block_reason).
    """
    if held_qty > 0:
        return True, "already_holding"
    sym = _sym_key(symbol)
    if not sym:
        return False, None
    try:
        from utils.si_adaptive_actions import unified_symbol_blocked

        blocked, reason = unified_symbol_blocked(sym)
        if blocked:
            return True, reason or "si_adaptive_block"
    except Exception:
        pass
    rec = (load_state().get("symbols") or {}).get(sym)
    if not isinstance(rec, dict):
        return False, None
    enter_ts = parse_iso(rec.get("last_enter_ts"))
    exit_ts = parse_iso(rec.get("last_exit_ts"))
    if enter_ts is None:
        return False, None
    if exit_ts is not None and exit_ts >= enter_ts:
        return False, None
    age = (datetime.now(timezone.utc) - enter_ts).total_seconds()
    if age < enter_cooldown_sec():
        return True, f"enter_cooldown:{sym}:{int(age)}s"
    return False, None


def record_enter(symbol: str) -> None:
    sym = _sym_key(symbol)
    if not sym:
        return
    doc = load_state()
    symbols = doc.setdefault("symbols", {})
    symbols[sym] = {
        "last_enter_ts": datetime.now(timezone.utc).isoformat(),
        "last_exit_ts": (symbols.get(sym) or {}).get("last_exit_ts") if isinstance(symbols.get(sym), dict) else None,
    }
    save_state(doc)


def record_exit(symbol: str) -> None:
    sym = _sym_key(symbol)
    if not sym:
        return
    doc = load_state()
    symbols = doc.setdefault("symbols", {})
    prev = symbols.get(sym) if isinstance(symbols.get(sym), dict) else {}
    symbols[sym] = {
        **prev,
        "last_exit_ts": datetime.now(timezone.utc).isoformat(),
    }
    save_state(doc)
