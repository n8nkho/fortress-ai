"""Alpaca paper vs live detection (same semantics as Classic Fortress)."""
from __future__ import annotations

import os
from typing import Any


def _strip_env_cred(v: str | None) -> str:
    """Trim whitespace and one pair of surrounding quotes (common in .env)."""
    s = (v or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def is_alpaca_paper() -> bool:
    base = (os.getenv("ALPACA_BASE_URL") or "").strip().lower()
    if not base:
        return True
    return "paper" in base


def alpaca_credentials() -> tuple[str, str]:
    """API key and secret from the environment (normalized)."""
    return _strip_env_cred(os.getenv("ALPACA_API_KEY")), _strip_env_cred(os.getenv("ALPACA_SECRET_KEY"))


def alpaca_trading_client_kwargs() -> dict[str, Any]:
    """Kwargs for ``TradingClient(key, secret, **here)``.

    ``alpaca-py`` does not read ``ALPACA_BASE_URL`` unless ``url_override`` is set,
    which caused paper/live or host mismatches vs the rest of the app.
    """
    base = (os.getenv("ALPACA_BASE_URL") or "").strip().rstrip("/")
    kw: dict[str, Any] = {"paper": is_alpaca_paper()}
    if base:
        kw["url_override"] = base
    return kw
