"""Scan oversized legacy positions and submit chunked reduce-only exit orders."""
from __future__ import annotations

from typing import Any

from unified_ai.legacy_flattener import flatten_oversized_positions


def flatten_oversized_legacy_positions(
    trading_client: Any,
    positions: list[Any] | None,
    *,
    dry_run: bool | None = None,
    equity: float | None = None,
) -> dict[str, Any]:
    """Trim positions whose notional exceeds caps via chunked market sells."""
    return flatten_oversized_positions(
        trading_client,
        positions,
        dry_run=dry_run,
        equity=equity,
    )


__all__ = ["flatten_oversized_legacy_positions", "flatten_oversized_positions"]
