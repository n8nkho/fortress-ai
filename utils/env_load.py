"""Two-phase .env load so FORTRESS_DOTENV_OVERRIDE in .env can force keys over shell exports."""
from __future__ import annotations

import os
from pathlib import Path


def load_fortress_dotenv(root: Path) -> None:
    """Load root/.env: first pass respects existing os.environ; if FORTRESS_DOTENV_OVERRIDE is set (from file or shell), second pass overrides."""
    env_path = root / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if not env_path.is_file():
        return
    load_dotenv(env_path, override=False)
    if str(os.environ.get("FORTRESS_DOTENV_OVERRIDE", "")).lower() in (
        "1",
        "true",
        "yes",
    ):
        load_dotenv(env_path, override=True)
