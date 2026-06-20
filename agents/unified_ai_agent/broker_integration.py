"""Alpaca order fill polling → exit event fill_status."""
from __future__ import annotations

from typing import Any

from agents.unified_ai_agent.exit_handler import EXIT_FILL_CONFIRMED, EXIT_UNFILLED


def resolve_fill_status(fill: dict[str, Any]) -> str:
    """Map Alpaca poll result to exit fill_status marker."""
    if int(fill.get("filled_qty") or 0) > 0:
        return EXIT_FILL_CONFIRMED
    return EXIT_UNFILLED


def build_order_submission(
    *,
    order_id: str,
    order_status: str,
    chunk_qty: int,
    fill: dict[str, Any],
) -> dict[str, Any]:
    fill_status = resolve_fill_status(fill)
    return {
        "id": order_id,
        "status": fill.get("status") or order_status,
        "qty": chunk_qty,
        "filled_qty": int(fill.get("filled_qty") or 0),
        "filled_avg_price": fill.get("filled_avg_price"),
        "fill_status": fill_status,
    }


def summarize_exit_fill_status(submitted: list[dict[str, Any]]) -> str:
    """Aggregate chunk fills into a single exit fill_status."""
    if any(int(o.get("filled_qty") or 0) > 0 for o in submitted):
        return EXIT_FILL_CONFIRMED
    return EXIT_UNFILLED
