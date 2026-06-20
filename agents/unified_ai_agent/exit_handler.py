"""Exit fill gating — PnL ledger only after broker fill confirmation."""
from __future__ import annotations

import logging
from typing import Any

EXIT_FILL_CONFIRMED = "exit_fill_confirmed"
EXIT_UNFILLED = "exit_unfilled"

_LOG = logging.getLogger("unified_ai_agent")


def should_record_exit_ledger(fill_status: str | None) -> bool:
    return str(fill_status or "").strip() == EXIT_FILL_CONFIRMED


def handle_exit_ledger(exit_event: dict[str, Any]) -> dict[str, Any]:
    """Record realized PnL only when fill_status is exit_fill_confirmed."""
    fill_status = str(exit_event.get("fill_status") or "")
    sym = str(exit_event.get("symbol") or "").upper()
    if not should_record_exit_ledger(fill_status):
        _LOG.warning(
            "premature_exit_ledger_blocked %s fill_status=%s block_reason=exit_unfilled",
            sym,
            fill_status or "missing",
        )
        return {
            "recorded": False,
            "block_reason": "exit_unfilled",
            "fill_status": fill_status or EXIT_UNFILLED,
            "detail": "premature_exit_ledger_blocked",
        }
    from agents.unified_ai_agent.ledger import record_confirmed_exit_fill

    return record_confirmed_exit_fill(exit_event)
