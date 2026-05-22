"""Runtime tunables for SPY intraday agent (data/spy_intraday/tunable_params_overrides.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.spy_agent_config import spy_data_dir


def override_path() -> Path:
    return spy_data_dir() / "tunable_params_overrides.json"


def load_overrides() -> dict[str, Any]:
    p = override_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_overrides(d: dict[str, Any]) -> None:
    spy_data_dir().mkdir(parents=True, exist_ok=True)
    override_path().write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")


def clear_overrides() -> None:
    p = override_path()
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def get_spy_min_confidence() -> float:
    try:
        base = float(os.environ.get("FORTRESS_SPY_MIN_CONFIDENCE", os.environ.get("FORTRESS_AI_MIN_CONFIDENCE", "0.85")) or 0.85)
    except ValueError:
        base = 0.85
    o = load_overrides().get("spy_min_confidence")
    if o is None:
        return max(0.65, min(0.92, base))
    try:
        return max(0.65, min(0.92, float(o)))
    except (TypeError, ValueError):
        return max(0.65, min(0.92, base))


def get_spy_loop_seconds_rth() -> float:
    try:
        base = float(os.environ.get("FORTRESS_SPY_LOOP_SECONDS", "300") or 300)
    except ValueError:
        base = 300.0
    o = load_overrides().get("spy_loop_seconds_rth")
    if o is None:
        return max(120.0, min(600.0, base))
    try:
        return max(120.0, min(600.0, float(o)))
    except (TypeError, ValueError):
        return max(120.0, min(600.0, base))


def get_spy_loop_seconds_active() -> float:
    try:
        base = float(os.environ.get("FORTRESS_SPY_LOOP_SECONDS_ACTIVE", "180") or 180)
    except ValueError:
        base = 180.0
    o = load_overrides().get("spy_loop_seconds_active")
    if o is None:
        return max(60.0, min(360.0, base))
    try:
        return max(60.0, min(360.0, float(o)))
    except (TypeError, ValueError):
        return max(60.0, min(360.0, base))


def get_spy_ladder_rungs() -> int:
    try:
        base = int(os.environ.get("FORTRESS_SPY_LADDER_RUNGS", "3") or 3)
    except ValueError:
        base = 3
    o = load_overrides().get("spy_ladder_rungs")
    if o is None:
        return max(2, min(4, base))
    try:
        return max(2, min(4, int(float(o))))
    except (TypeError, ValueError):
        return max(2, min(4, base))


def current_snapshot() -> dict[str, Any]:
    return {
        "spy_min_confidence": get_spy_min_confidence(),
        "spy_loop_seconds_rth": get_spy_loop_seconds_rth(),
        "spy_loop_seconds_active": get_spy_loop_seconds_active(),
        "spy_ladder_rungs": get_spy_ladder_rungs(),
    }
