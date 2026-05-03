"""Runtime tunable parameters (data/tunable_params_overrides.json) — written only by self-improvement approval."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data"


def override_path() -> Path:
    return _data_dir() / "tunable_params_overrides.json"


def load_overrides() -> dict[str, Any]:
    p = override_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_overrides(d: dict[str, Any]) -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    override_path().write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def clear_overrides() -> None:
    p = override_path()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def get_confidence_threshold() -> float:
    """Env default + optional override (clamped to engineering bounds)."""
    try:
        base = float(os.environ.get("FORTRESS_AI_MIN_CONFIDENCE", "0.8"))
    except ValueError:
        base = 0.8
    o = load_overrides().get("confidence_threshold")
    if o is None:
        return max(0.6, min(0.95, base))
    try:
        return max(0.6, min(0.95, float(o)))
    except (TypeError, ValueError):
        return max(0.6, min(0.95, base))


def get_decision_interval_seconds(cli_override: float | None) -> float | None:
    """Returns None to mean 'use effective_loop_interval_seconds as-is'."""
    if cli_override is not None:
        return float(cli_override)
    o = load_overrides().get("decision_interval")
    if o is None:
        return None
    try:
        return max(120.0, min(1800.0, float(o)))
    except (TypeError, ValueError):
        return None


def get_position_size_pct() -> float:
    """Fraction of portfolio/equity guidance — advisory until sizing logic consumes it (clamped)."""
    try:
        cap = float(os.environ.get("FORTRESS_MAX_POSITION_SIZE_PCT", "0.03"))
    except ValueError:
        cap = 0.03
    try:
        base = float(os.environ.get("FORTRESS_POSITION_SIZE_PCT", str(cap)))
    except ValueError:
        base = cap
    base = max(0.02, min(cap, base))
    o = load_overrides().get("position_size_pct")
    if o is None:
        return base
    try:
        return max(0.02, min(cap, float(o)))
    except (TypeError, ValueError):
        return base


def get_rsi_entry_threshold_int() -> int:
    try:
        base = int(float(os.environ.get("FORTRESS_AI_RSI_ENTRY_THRESHOLD", "43")))
    except ValueError:
        base = 43
    o = load_overrides().get("rsi_entry_threshold")
    if o is None:
        return max(35, min(50, base))
    try:
        return max(35, min(50, int(float(o))))
    except (TypeError, ValueError):
        return max(35, min(50, base))
