"""Risk-layer duplicate-entry gate (complements utils/pre_trade_gate — does not weaken caps)."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def duplicate_entry_gate_enabled() -> bool:
    """True when POSITION_DEDUPLICATION_ENABLED is on (env or config default)."""
    try:
        from unified_ai.settings import position_deduplication_enabled

        return position_deduplication_enabled()
    except Exception:
        return True


def evaluate_duplicate_entry_gate(
    *,
    side: str,
    symbol: str,
    positions: list[dict[str, Any]] | None = None,
    held_qty: int | None = None,
) -> dict[str, Any]:
    """
    Block BUY when symbol is already held (duplicate_entry_accumulation mitigation).

    Configurable via ENFORCE_POSITION_DEDUPLICATION / POSITION_DEDUPLICATION_ENABLED.
    Does not replace notional/halt gates.
    """
    sd = (side or "").strip().upper()
    if sd != "BUY":
        return {"allowed": True, "reasons": []}
    if not duplicate_entry_gate_enabled():
        return {"allowed": True, "reasons": []}

    sym = (symbol or "").strip().upper()
    if not sym:
        return {"allowed": False, "reasons": ["missing_symbol"], "block_reason": "missing_symbol"}

    from risk.position_manager import PositionManager

    pm = PositionManager(list(positions or []))
    hq = held_qty
    if hq is None:
        pos = pm.get_position(sym)
        if pos is not None:
            try:
                hq = int(abs(float(pos.get("qty") or 0)))
            except (TypeError, ValueError):
                hq = 0
        else:
            hq = 0

    if hq > 0 or pm.has_open_position(sym):
        detail = f"already_holding:{sym}:{hq if hq > 0 else 'pending'}"
        log.warning(
            "duplicate_entry_accumulation already_holding:%s entry_blocked_by_cooldown %s",
            sym,
            detail,
        )
        return {
            "allowed": False,
            "reasons": ["duplicate_entry_accumulation", f"already_holding:{sym}"],
            "block_reason": "already_holding",
            "detail": detail,
        }

    try:
        from utils.unified_enter_guard import entry_blocked_by_cooldown

        blocked, reason = entry_blocked_by_cooldown(sym, held_qty=hq or 0)
    except Exception:
        blocked, reason = False, None

    if blocked:
        br = "already_holding" if reason == "already_holding" else "enter_cooldown"
        if br == "enter_cooldown":
            log.warning("enter_cooldown entry_blocked_by_cooldown %s", reason)
        return {
            "allowed": False,
            "reasons": [reason or br],
            "block_reason": br,
            "detail": reason or br,
        }

    return {"allowed": True, "reasons": []}
