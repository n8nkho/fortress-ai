"""Configuration for the SPY/DIA intraday index day-trading agent."""
from __future__ import annotations

import os
from pathlib import Path


def spy_data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_SPY_DATA_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
    else:
        root = Path(__file__).resolve().parent.parent
        base = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
        p = Path(base).expanduser() if base else root / "data"
        p = p / "spy_intraday"
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_symbol() -> str:
    """Tradable ETF: SPY (default) or DIA (Dow proxy)."""
    sym = (os.environ.get("FORTRESS_INDEX_SYMBOL") or os.environ.get("FORTRESS_SPY_AGENT_SYMBOL") or "SPY").strip().upper()
    return sym[:12] if sym else "SPY"


def max_exposure_usd() -> float:
    try:
        return max(100.0, float(os.environ.get("FORTRESS_SPY_MAX_EXPOSURE_USD", "10000") or 10000))
    except ValueError:
        return 10000.0


def ladder_rungs() -> int:
    try:
        from utils.spy_tunable_overrides import get_spy_ladder_rungs

        return get_spy_ladder_rungs()
    except Exception:
        try:
            return max(1, min(5, int(os.environ.get("FORTRESS_SPY_LADDER_RUNGS", "3") or 3)))
        except ValueError:
            return 3


def rung_notional_usd() -> float:
    return max_exposure_usd() / float(ladder_rungs())


def loop_seconds_rth() -> float:
    try:
        from utils.spy_tunable_overrides import get_spy_loop_seconds_rth

        return get_spy_loop_seconds_rth()
    except Exception:
        try:
            return max(60.0, float(os.environ.get("FORTRESS_SPY_LOOP_SECONDS", "300") or 300))
        except ValueError:
            return 300.0


def loop_seconds_active() -> float:
    """Shorter cadence during core intraday window (10:00–15:30 ET)."""
    try:
        from utils.spy_tunable_overrides import get_spy_loop_seconds_active

        return get_spy_loop_seconds_active()
    except Exception:
        try:
            return max(60.0, float(os.environ.get("FORTRESS_SPY_LOOP_SECONDS_ACTIVE", "180") or 180))
        except ValueError:
            return 180.0


def min_confidence() -> float:
    try:
        from utils.spy_tunable_overrides import get_spy_min_confidence

        return get_spy_min_confidence()
    except Exception:
        try:
            return float(
                os.environ.get("FORTRESS_SPY_MIN_CONFIDENCE", os.environ.get("FORTRESS_AI_MIN_CONFIDENCE", "0.85")) or 0.85
            )
        except ValueError:
            return 0.85


def dry_run() -> bool:
    return str(os.environ.get("FORTRESS_SPY_DRY_RUN", os.environ.get("FORTRESS_AI_DRY_RUN", "1"))).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def manual_only() -> bool:
    return str(os.environ.get("FORTRESS_SPY_MANUAL_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")


def on_demand_flag_path() -> Path:
    raw = (os.environ.get("FORTRESS_SPY_ON_DEMAND_FLAG") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return spy_data_dir() / "on_demand_cycle.flag"


def instance_name() -> str:
    return (os.environ.get("FORTRESS_SPY_INSTANCE_NAME") or "Fortress-SPY-Intraday").strip() or "Fortress-SPY-Intraday"
