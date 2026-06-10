"""Backward-compatible re-exports — see utils/swarm_clip_ladder.py."""
from __future__ import annotations

from utils.swarm_clip_ladder import (  # noqa: F401
    authorize_add_clip,
    clip_ladder_enabled,
    clip_min_gap_sec,
    clip_min_hold_sec,
    clip_size,
    effective_max_shares,
    max_shares_per_symbol,
)
