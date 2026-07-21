"""Constructive-tape participation override for market-relative entry blocks.

On strong SPY tape days with participation shortfall, allow bounded entry
when alpha is not deeply negative. Deep-alpha floor and daily clip caps
keep risk rails intact.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_MARKER = "constructive_tape_entry_override"
_DEEP_FLOOR_DEFAULT = -1.0
_SOFT_THRESHOLD_DEFAULT = -0.8
_MAX_OVERRIDES_DEFAULT = 6


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent.parent
    return Path(raw) if raw else (root / "data")


def _state_path() -> Path:
    return _data_dir() / "portfolio_session" / "constructive_tape_override.json"


def constructive_tape_override_enabled() -> bool:
    return str(os.environ.get("FORTRESS_CONSTRUCTIVE_TAPE_OVERRIDE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def deep_alpha_floor() -> float:
    try:
        return float(os.environ.get("FORTRESS_MR_DEEP_ALPHA_FLOOR", str(_DEEP_FLOOR_DEFAULT)) or _DEEP_FLOOR_DEFAULT)
    except (TypeError, ValueError):
        return _DEEP_FLOOR_DEFAULT


def soft_alpha_threshold() -> float:
    try:
        return float(
            os.environ.get("FORTRESS_MR_SOFT_ALPHA_THRESHOLD", str(_SOFT_THRESHOLD_DEFAULT))
            or _SOFT_THRESHOLD_DEFAULT
        )
    except (TypeError, ValueError):
        return _SOFT_THRESHOLD_DEFAULT


def max_overrides_per_day() -> int:
    try:
        return max(0, int(os.environ.get("FORTRESS_MR_TAPE_OVERRIDE_MAX_PER_DAY", str(_MAX_OVERRIDES_DEFAULT)) or _MAX_OVERRIDES_DEFAULT))
    except (TypeError, ValueError):
        return _MAX_OVERRIDES_DEFAULT


def _session_date_et() -> str:
    return datetime.now(_ET).date().isoformat()


def _load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_state(doc: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def overrides_used_today() -> int:
    st = _load_state()
    if str(st.get("session_date_et") or "") != _session_date_et():
        return 0
    try:
        return int(st.get("override_count") or 0)
    except (TypeError, ValueError):
        return 0


def record_override(*, alpha: float, detail: str) -> None:
    today = _session_date_et()
    st = _load_state()
    if str(st.get("session_date_et") or "") != today:
        st = {"session_date_et": today, "override_count": 0, "events": []}
    st["override_count"] = int(st.get("override_count") or 0) + 1
    events = list(st.get("events") or [])
    events.append(
        {
            "ts": datetime.now(_ET).isoformat(),
            "alpha": round(float(alpha), 4),
            "detail": str(detail)[:160],
            "marker": _MARKER,
        }
    )
    st["events"] = events[-20:]
    st["marker"] = _MARKER
    _save_state(st)


def _session_context_flags(session_state: dict[str, Any] | None) -> dict[str, Any]:
    """Read strong-tape / shortfall / expectancy from session_state only (fail-closed).

    Callers (RiskManager.build_session_state) must populate strong_tape_1d and
    participation_shortfall_exits. Missing flags → no override (do not hit live
    portfolio APIs from the gate path — keeps unit tests deterministic).
    """
    state = dict(session_state or {})
    strong = state.get("strong_tape_1d")
    shortfall = state.get("participation_shortfall_exits")
    exp = state.get("session_expectancy_usd")
    exits = state.get("session_exit_count")
    try:
        shortfall_i = int(shortfall or 0)
    except (TypeError, ValueError):
        shortfall_i = 0
    try:
        exits_i = int(exits or 0)
    except (TypeError, ValueError):
        exits_i = 0
    try:
        exp_f = float(exp) if exp is not None else None
    except (TypeError, ValueError):
        exp_f = None
    return {
        "strong_tape_1d": bool(strong),
        "participation_shortfall_exits": shortfall_i,
        "session_exit_count": exits_i,
        "session_expectancy_usd": exp_f,
        "alpha_vs_spy_pct": state.get("alpha_vs_spy_pct"),
    }


def maybe_allow_despite_underperformance(
    alpha: float,
    *,
    hard_threshold: float,
    session_state: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    Return (allow_entry, reason).

    allow_entry=True means skip the market-relative block (override).
    """
    if not constructive_tape_override_enabled():
        return False, "override_disabled"

    floor = deep_alpha_floor()
    if float(alpha) < floor:
        return False, f"deep_alpha_floor alpha={alpha:.4f}<{floor:.4f}"

    # Still worse than hard threshold but within soft band — only override on constructive tape.
    soft = soft_alpha_threshold()
    # If alpha is better than soft threshold, gate shouldn't have blocked; no override needed.
    if float(alpha) >= soft:
        return False, "alpha_above_soft_threshold"

    flags = _session_context_flags(session_state)
    if not flags["strong_tape_1d"]:
        return False, "not_strong_tape"
    if flags["participation_shortfall_exits"] <= 0:
        return False, "no_participation_shortfall"

    exp = flags["session_expectancy_usd"]
    if exp is not None and exp < -0.05 and flags["session_exit_count"] >= 4:
        return False, f"session_bleeding exp={exp:.4f}"

    used = overrides_used_today()
    cap = max_overrides_per_day()
    if used >= cap:
        return False, f"override_cap_reached used={used} cap={cap}"

    detail = (
        f"{_MARKER} alpha={float(alpha):.4f} hard={float(hard_threshold):.4f} "
        f"soft={soft:.4f} floor={floor:.4f} shortfall={flags['participation_shortfall_exits']} "
        f"used={used + 1}/{cap}"
    )
    record_override(alpha=float(alpha), detail=detail)
    log.info("%s tape_override %s", _MARKER, detail)
    return True, detail


__all__ = [
    "constructive_tape_override_enabled",
    "deep_alpha_floor",
    "maybe_allow_despite_underperformance",
    "max_overrides_per_day",
    "overrides_used_today",
    "soft_alpha_threshold",
]
