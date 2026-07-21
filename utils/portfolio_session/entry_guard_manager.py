"""Portfolio session entry guard manager — backward-compatible re-exports."""
from __future__ import annotations

from utils.portfolio_session.entry_block_manager import (
    EntryBlockManager,
    evaluate_entry_guard_blocks,
    evaluate_entry_guards_loop,
    get_entry_block_manager,
    get_entry_guards,
)

__all__ = [
    "EntryBlockManager",
    "evaluate_entry_guard_blocks",
    "evaluate_entry_guards_loop",
    "get_entry_block_manager",
    "get_entry_guards",
]
