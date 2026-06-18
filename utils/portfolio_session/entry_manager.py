"""Track per-session entry block counts (denylist, pause_entries, pattern_disables)."""
from __future__ import annotations

from typing import Any

_BLOCK_TYPES = ("denylist", "pause_entries", "pattern_disables")


def _classify_block(reasoning: str) -> str | None:
    r = str(reasoning or "").strip().lower()
    if not r:
        return None
    if r == "manual_denylist" or r.endswith("_denylist") or r == "denylist":
        return "denylist"
    if r in ("pause_entries", "swarm_session_critical_pause") or r.startswith("pause_"):
        return "pause_entries"
    if r.startswith("pattern_disabled") or "pattern_disable" in r:
        return "pattern_disables"
    return None


class EntryManager:
    def __init__(self) -> None:
        self._block_counts: dict[str, int] = {k: 0 for k in _BLOCK_TYPES}

    def evaluate_entry_blocks(
        self,
        reasoning: str,
        *,
        action: str = "wait",
        side: str = "flat",
        executed: bool | None = None,
    ) -> str | None:
        """Increment block counter when a flat-side entry attempt is rejected."""
        if str(side or "flat") != "flat":
            return None
        act = str(action or "wait")
        if act in ("enter_long", "enter_short") and executed is not False:
            return None
        block_type = _classify_block(reasoning)
        if not block_type:
            return None
        self._block_counts[block_type] = int(self._block_counts.get(block_type) or 0) + 1
        return block_type

    def block_counts(self) -> dict[str, int]:
        return dict(self._block_counts)

    def reset_counts(self) -> None:
        self._block_counts = {k: 0 for k in _BLOCK_TYPES}


_manager = EntryManager()


def get_entry_manager() -> EntryManager:
    return _manager


def record_entry_block(
    decision: dict[str, Any],
    act_result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
) -> str | None:
    """Convenience hook for swarm workers after decide/act."""
    side = str((features or {}).get("side") or "flat")
    action = str(decision.get("action") or "wait")
    reasoning = str(act_result.get("block_reason") or decision.get("reasoning") or "")
    executed = act_result.get("executed")
    return get_entry_manager().evaluate_entry_blocks(
        reasoning,
        action=action,
        side=side,
        executed=executed if executed is not None else None,
    )
