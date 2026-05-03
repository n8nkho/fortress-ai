"""Alpaca paper vs live detection (same semantics as Classic Fortress)."""
from __future__ import annotations

import os


def is_alpaca_paper() -> bool:
    base = (os.getenv("ALPACA_BASE_URL") or "").strip().lower()
    if not base:
        return True
    return "paper" in base
