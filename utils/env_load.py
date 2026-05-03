"""Two-phase .env load so FORTRESS_DOTENV_OVERRIDE in .env can force keys over shell exports."""
from __future__ import annotations

import os
from pathlib import Path


def load_fortress_dotenv(root: Path) -> None:
    """Load root/.env: first pass respects existing os.environ; second pass overrides when requested or when Alpaca keys are still missing.

    Empty shell exports (e.g. ALPACA_API_KEY="") count as "set" for override=False and block values from .env — second pass fixes that.
    """
    env_path = root / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if not env_path.is_file():
        return
    load_dotenv(env_path, override=False)
    override_requested = str(os.environ.get("FORTRESS_DOTENV_OVERRIDE", "")).lower() in (
        "1",
        "true",
        "yes",
    )
    key = (os.environ.get("ALPACA_API_KEY") or "").strip()
    sec = (os.environ.get("ALPACA_SECRET_KEY") or "").strip()
    if override_requested or not key or not sec:
        load_dotenv(env_path, override=True)
