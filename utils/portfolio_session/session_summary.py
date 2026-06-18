"""Aggregate portfolio session summary including entry block breakdown."""
from __future__ import annotations

from typing import Any

from utils.portfolio_session.entry_manager import get_entry_manager


def _signal_side(signal: dict[str, Any]) -> str:
    features = signal.get("features") if isinstance(signal.get("features"), dict) else {}
    return str(features.get("side") or signal.get("side") or "flat")


def _signal_decision(signal: dict[str, Any]) -> dict[str, Any]:
    dec = signal.get("decision")
    return dec if isinstance(dec, dict) else signal


def _signal_act(signal: dict[str, Any]) -> dict[str, Any]:
    act = signal.get("act") or signal.get("act_result")
    return act if isinstance(act, dict) else {}


def generate_summary(
    *,
    signals: list[dict[str, Any]] | None = None,
    entry_manager=None,
) -> dict[str, Any]:
    """Build session summary; replay signals when provided, else use live counters."""
    em = entry_manager or get_entry_manager()
    if signals:
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            dec = _signal_decision(signal)
            act = _signal_act(signal)
            em.evaluate_entry_blocks(
                str(act.get("block_reason") or dec.get("reasoning") or signal.get("reasoning") or ""),
                action=str(dec.get("action") or signal.get("action") or "wait"),
                side=_signal_side(signal),
                executed=act.get("executed"),
            )
    return {
        "entry_block_breakdown": em.block_counts(),
    }
