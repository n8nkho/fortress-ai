"""Shared paths for historical_seeder."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent.parent


def prices_dir() -> Path:
    return repo_root() / "data" / "historical" / "prices"


def features_dir() -> Path:
    return repo_root() / "data" / "historical" / "features"


def patterns_dir() -> Path:
    return repo_root() / "data" / "historical" / "patterns"


def pattern_results_path() -> Path:
    return patterns_dir() / "pattern_results.json"


def ingest_health_path() -> Path:
    return repo_root() / "data" / "domain_intelligence" / "ingest_health.json"
