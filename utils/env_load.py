"""Load Fortress .env — file is source of truth for systemd services and local runs."""
from __future__ import annotations

from pathlib import Path


def load_fortress_dotenv(root: Path) -> None:
    """Load root/.env into os.environ.

    Uses override=True so empty shell exports and bare systemd units still pick up
    FORTRESS_SKIM_DRY_RUN=0, Alpaca keys, etc. Set vars in the shell before calling
    only when you intentionally want to override .env for a one-off run.
    """
    env_path = root / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if not env_path.is_file():
        return
    load_dotenv(env_path, override=True)
