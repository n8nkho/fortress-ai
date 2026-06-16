"""Session loser auto-pause — stop churn on symbols bleeding today."""
from __future__ import annotations

import os
from typing import Any

from utils.system_time import now_iso


def session_loser_pause_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SESSION_LOSER_PAUSE", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _config(component: str) -> Any:
    if component == "skim_swarm":
        from utils import skim_swarm_config as cfg

        return cfg
    if component == "infra_swarm":
        from utils import infra_swarm_config as cfg

        return cfg
    raise ValueError(f"unknown component: {component}")


def session_loser_min_losses(component: str) -> int:
    cfg = _config(component)
    prefix = "SKIM" if component == "skim_swarm" else "INFRA"
    raw = (os.environ.get(f"FORTRESS_{prefix}_SESSION_LOSER_MIN_LOSSES") or "").strip()
    if raw:
        try:
            return max(2, int(raw))
        except ValueError:
            pass
    try:
        return max(2, int(getattr(cfg, "symbol_pause_min_exits", lambda: 4)()) - 1)
    except Exception:
        return 4


def session_loser_min_pnl_usd(component: str) -> float:
    cfg = _config(component)
    prefix = "SKIM" if component == "skim_swarm" else "INFRA"
    raw = (os.environ.get(f"FORTRESS_{prefix}_SESSION_LOSER_MIN_PNL_USD") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        return max(-2.0, float(getattr(cfg, "symbol_pause_min_pnl_usd", lambda: -1.0)()) * 0.35)
    except Exception:
        return -0.25


def should_pause_session_loser(stats: dict[str, Any], *, component: str) -> tuple[bool, str]:
    if not session_loser_pause_enabled():
        return False, ""
    exits = int(stats.get("exits") or 0)
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    closed = max(wins + losses, 1)
    win_rate = wins / closed
    pnl = float(stats.get("sum_pnl_usd") or 0)
    min_losses = session_loser_min_losses(component)
    min_pnl = session_loser_min_pnl_usd(component)

    if losses >= min_losses and pnl <= min_pnl:
        return True, f"session_loser losses={losses} pnl={pnl:.2f}<={min_pnl:.2f}"

    cfg = _config(component)
    try:
        min_ex = int(cfg.symbol_pause_min_exits())
        min_pnl_full = float(cfg.symbol_pause_min_pnl_usd())
        wr_max = float(cfg.symbol_pause_win_rate())
    except Exception:
        return False, ""

    if exits >= min_ex and pnl <= min_pnl_full and win_rate < wr_max:
        return True, f"session_loser wr={win_rate:.2f} pnl={pnl:.2f}"
    return False, ""


def apply_session_loser_pause_to_params(
    params: dict[str, Any],
    stats: dict[str, Any],
    *,
    component: str,
) -> str | None:
    pause, reason = should_pause_session_loser(stats, component=component)
    if pause and not params.get("pause_entries"):
        params["pause_entries"] = True
        return reason
    return None


def apply_session_loser_pause(component: str) -> dict[str, Any]:
    """Scan learned symbols and pause session bleeders (entries only; exits still run)."""
    if not session_loser_pause_enabled():
        return {"component": component, "paused": [], "skipped": "disabled"}

    from pathlib import Path

    from utils.edge_autofix import _swarm_dir

    learned_dir = _swarm_dir(component) / "learned"
    if not learned_dir.is_dir():
        return {"component": component, "paused": [], "skipped": "no_learned"}

    if component == "skim_swarm":
        from agents.skim_swarm.symbol_learning import load_learned, save_learned
    else:
        from agents.infra_swarm.symbol_learning import load_learned, save_learned

    paused: list[dict[str, str]] = []
    for path in sorted(learned_dir.glob("*.json")):
        sym = path.stem.upper()
        try:
            doc = load_learned(sym)
        except Exception:
            continue
        stats = doc.get("session_stats") or {}
        params = doc.setdefault("params", {})
        note = apply_session_loser_pause_to_params(params, stats, component=component)
        if note:
            doc["params"] = params
            notes = list(doc.get("notes") or [])
            notes.append(f"session_loser_pause:{note}")
            doc["notes"] = notes[-12:]
            doc["session_loser_pause_utc"] = now_iso()
            save_learned(sym, doc)
            paused.append({"symbol": sym, "reason": note})

    return {"component": component, "paused": paused, "markers": ["session_loser_pause"]}
