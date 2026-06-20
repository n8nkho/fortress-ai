"""PnL ledger writes gated on exit_fill_confirmed."""
from __future__ import annotations

import logging
from typing import Any

from agents.unified_ai_agent.exit_handler import EXIT_FILL_CONFIRMED

_LOG = logging.getLogger("unified_ai_agent")


class PrematureExitLedgerError(ValueError):
    """Ledger write rejected because broker fill was not confirmed."""


def validate_fill_status_for_ledger(fill_status: str | None) -> None:
    status = str(fill_status or "").strip()
    if status != EXIT_FILL_CONFIRMED:
        raise PrematureExitLedgerError(
            f"premature_exit_ledger_rejected fill_status={status or 'missing'} "
            f"expected={EXIT_FILL_CONFIRMED}"
        )


def record_confirmed_exit_fill(exit_event: dict[str, Any]) -> dict[str, Any]:
    """Append a realized fill row only when fill_status is confirmed."""
    validate_fill_status_for_ledger(exit_event.get("fill_status"))
    from utils.ai_pnl_ledger import append_realized_fill

    extra = dict(exit_event.get("extra") or {})
    extra.setdefault("action", "exit_position")
    extra["fill_status"] = EXIT_FILL_CONFIRMED
    extra["note"] = EXIT_FILL_CONFIRMED

    rec = append_realized_fill(
        symbol=str(exit_event["symbol"]),
        pnl_usd=float(exit_event.get("pnl_usd") or 0),
        side=str(exit_event.get("side") or "SELL"),
        qty=int(exit_event.get("qty") or 0),
        order_id=str(exit_event.get("order_id") or "") or None,
        extra=extra,
    )
    _LOG.info(
        "exit_fill_confirmed %s qty=%s order_id=%s",
        exit_event.get("symbol"),
        exit_event.get("qty"),
        exit_event.get("order_id"),
    )
    return {"recorded": True, "ledger_entry": rec, "fill_status": EXIT_FILL_CONFIRMED}
