"""Swarm-level session SI — detect negative edge / over-churn and auto-tighten."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _swarm_dir(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name


def session_policy_path(component: str) -> Path:
    return _swarm_dir(component) / "session_policy.json"


def _default_policy() -> dict[str, Any]:
    return {
        "mode": "normal",
        "negative_edge": False,
        "over_churn": False,
        "pause_new_entries": False,
        "max_open_effective": None,
        "max_l1_gross_effective": None,
        "enter_long_delta_boost": 0.0,
        "enter_short_delta_boost": 0.0,
        "cycle_interval_mult": 1.0,
        "session_exits": 0,
        "session_wins": 0,
        "session_losses": 0,
        "session_win_rate": None,
        "session_expectancy_usd": None,
        "session_pnl_usd": None,
        "notes": [],
    }


def load_session_policy(component: str) -> dict[str, Any]:
    p = session_policy_path(component)
    if not p.exists():
        return _default_policy()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        out = _default_policy()
        if isinstance(doc, dict):
            out.update(doc)
        return out
    except Exception:
        return _default_policy()


def save_session_policy(component: str, policy: dict[str, Any]) -> None:
    p = session_policy_path(component)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(policy, indent=2), encoding="utf-8")


def _config(component: str) -> Any:
    if component == "skim_swarm":
        from utils import skim_swarm_config as cfg

        return cfg
    if component == "infra_swarm":
        from utils import infra_swarm_config as cfg

        return cfg
    raise ValueError(f"unknown component: {component}")


def _session_date(component: str) -> str:
    if component == "skim_swarm":
        from agents.skim_swarm.eod import session_date_et

        return session_date_et()
    from agents.infra_swarm.eod import session_date_et

    return session_date_et()


def aggregate_session_stats(component: str) -> dict[str, Any]:
    """Sum session_stats across all learned symbol files for today."""
    learned_dir = _swarm_dir(component) / "learned"
    sess = _session_date(component)
    totals = {
        "decisions": 0,
        "entries": 0,
        "exits": 0,
        "wins": 0,
        "losses": 0,
        "sum_pnl_usd": 0.0,
        "symbols_with_exits": 0,
    }
    if not learned_dir.is_dir():
        return totals
    for f in learned_dir.glob("*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if doc.get("session_date_et") != sess:
            continue
        st = doc.get("session_stats") or {}
        ex = int(st.get("exits") or 0)
        totals["decisions"] += int(st.get("decisions") or 0)
        totals["entries"] += int(st.get("entries") or 0)
        totals["exits"] += ex
        totals["wins"] += int(st.get("wins") or 0)
        totals["losses"] += int(st.get("losses") or 0)
        totals["sum_pnl_usd"] += float(st.get("sum_pnl_usd") or 0)
        if ex > 0:
            totals["symbols_with_exits"] += 1
    closed = totals["wins"] + totals["losses"]
    totals["win_rate"] = (totals["wins"] / closed) if closed else None
    totals["expectancy_usd"] = (totals["sum_pnl_usd"] / totals["exits"]) if totals["exits"] else None
    return totals


def swarm_session_si_enabled(component: str) -> bool:
    cfg = _config(component)
    key = "FORTRESS_SKIM_SWARM_SESSION_SI" if component == "skim_swarm" else "FORTRESS_INFRA_SWARM_SESSION_SI"
    return str(os.environ.get(key, "1")).strip().lower() in ("1", "true", "yes", "on")


def _churn_limits(component: str) -> tuple[int, float, int]:
    cfg = _config(component)
    prefix = "FORTRESS_SKIM" if component == "skim_swarm" else "FORTRESS_INFRA"
    try:
        max_exits = int(os.environ.get(f"{prefix}_CHURN_MAX_EXITS_SESSION", "22" if component == "skim_swarm" else "12"))
    except ValueError:
        max_exits = 22 if component == "skim_swarm" else 12
    try:
        min_wr = float(os.environ.get(f"{prefix}_CHURN_MIN_WIN_RATE", "0.38"))
    except ValueError:
        min_wr = 0.38
    try:
        min_exits = int(os.environ.get(f"{prefix}_CHURN_MIN_EXITS", "8" if component == "skim_swarm" else "6"))
    except ValueError:
        min_exits = 8 if component == "skim_swarm" else 6
    return max_exits, min_wr, min_exits


def adapt_swarm_session(
    component: str,
    *,
    day_realized_pnl: float | None = None,
) -> dict[str, Any]:
    """
    Swarm-level SI: tighten entries / cap slots when session shows negative edge or over-churn.

    Per-symbol SI optimizes pattern share; this layer catches session-wide bleed and churn.
    """
    if not swarm_session_si_enabled(component):
        return load_session_policy(component)

    cfg = _config(component)
    stats = aggregate_session_stats(component)
    exits = int(stats.get("exits") or 0)
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    closed = wins + losses
    wr = stats.get("win_rate")
    exp = stats.get("expectancy_usd")
    pnl = float(stats.get("sum_pnl_usd") or 0)
    if day_realized_pnl is not None:
        pnl = float(day_realized_pnl)

    churn_max, churn_min_wr, churn_min_exits = _churn_limits(component)
    exp_floor = float(cfg.session_expectancy_min_usd())
    base_max_open = int(cfg.max_open_positions())

    try:
        from utils.edge_scorecard import load_scorecard

        sc = load_scorecard(component)
        pay = sc.get("payoff_ratio")
        if sc.get("ok") and pay is not None and float(pay) < 0.9 and int(sc.get("exits") or 0) >= 8:
            exp_floor = max(exp_floor, 0.0)
    except Exception:
        pass

    negative_edge = exits >= churn_min_exits and exp is not None and float(exp) < exp_floor
    over_churn = (
        exits >= churn_max
        and wr is not None
        and float(wr) < churn_min_wr
        and (exp is None or float(exp) <= 0)
    )
    severe = (
        exits >= churn_max + 8
        or (pnl <= float(cfg.daily_stop_usd()) * 0.55)
        or (negative_edge and over_churn)
    )

    prev = load_session_policy(component)
    notes: list[str] = []

    if not negative_edge and not over_churn:
        mode = "normal"
        policy = _default_policy()
        adaptive_doc: dict[str, Any] = {}
        try:
            from utils.adaptive_max_open import compute_adaptive_max_open

            adaptive_doc = compute_adaptive_max_open(component)
        except Exception:
            pass
        policy.update(
            {
                "mode": mode,
                "max_open_adaptive": adaptive_doc.get("effective"),
                "max_open_boost": adaptive_doc.get("boost"),
                "session_exits": exits,
                "session_wins": wins,
                "session_losses": losses,
                "session_win_rate": round(wr, 4) if wr is not None else None,
                "session_expectancy_usd": round(exp, 4) if exp is not None else None,
                "session_pnl_usd": round(pnl, 4),
                "notes": ["swarm_session_recovered"] if prev.get("mode") != "normal" else [],
            }
        )
        if adaptive_doc.get("markers"):
            notes_list = list(policy.get("notes") or [])
            notes_list.append(
                f"adaptive_max_open={adaptive_doc.get('effective')} "
                f"({','.join(adaptive_doc.get('markers') or [])})"
            )
            policy["notes"] = notes_list[-8:]
    else:
        if severe:
            mode = "critical"
            max_open_eff = max(2, base_max_open - 3)
            long_boost = 0.06
            short_boost = -0.06
            interval_mult = 1.28
            pause_entries = True
        elif over_churn:
            mode = "churn"
            max_open_eff = max(2, base_max_open - 2)
            long_boost = 0.04
            short_boost = -0.04
            interval_mult = 1.18
            pause_entries = False
        else:
            mode = "tight"
            max_open_eff = max(2, base_max_open - 1)
            long_boost = 0.025
            short_boost = -0.025
            interval_mult = 1.10
            pause_entries = False

        if negative_edge:
            notes.append(f"negative_edge exp={exp:.3f}<{exp_floor:.3f}")
        if over_churn:
            notes.append(f"over_churn exits={exits} wr={wr:.2f}<{churn_min_wr:.2f}")

        max_l1_eff = None
        if component == "infra_swarm":
            base_l1 = int(cfg.max_l1_gross_long())
            if mode == "critical":
                max_l1_eff = max(1, base_l1 - 2)
            elif mode in ("churn", "tight"):
                max_l1_eff = max(1, base_l1 - 1)
            if max_l1_eff is not None and max_l1_eff < base_l1:
                notes.append(f"max_l1_gross {base_l1}->{max_l1_eff}")

        policy = {
            "mode": mode,
            "negative_edge": negative_edge,
            "over_churn": over_churn,
            "pause_new_entries": pause_entries,
            "max_open_effective": max_open_eff,
            "max_l1_gross_effective": max_l1_eff,
            "enter_long_delta_boost": long_boost,
            "enter_short_delta_boost": short_boost,
            "cycle_interval_mult": interval_mult,
            "session_exits": exits,
            "session_wins": wins,
            "session_losses": losses,
            "session_win_rate": round(wr, 4) if wr is not None else None,
            "session_expectancy_usd": round(exp, 4) if exp is not None else None,
            "session_pnl_usd": round(pnl, 4),
            "notes": notes + [f"swarm_session_{mode}"],
        }

    policy["updated_utc"] = datetime.now(timezone.utc).isoformat()
    save_session_policy(component, policy)

    prev_mode = str(prev.get("mode") or "normal")
    new_mode = str(policy.get("mode") or "normal")
    if new_mode != prev_mode or new_mode != "normal":
        try:
            from utils.si_capability_review import collect_metrics
            from utils.si_intervention_log import record_intervention

            record_intervention(
                component=component,
                action=f"swarm_session_{new_mode}",
                metrics_snapshot=collect_metrics(),
                detail={
                    "mode": new_mode,
                    "prev_mode": prev_mode,
                    "max_open_effective": policy.get("max_open_effective"),
                },
            )
        except Exception:
            pass

    if policy.get("mode") != "normal":
        pass  # policy persisted in session_policy.json for integrity + agents

    return policy


def effective_max_open(component: str) -> int:
    from utils.adaptive_max_open import adaptive_max_open_value

    adaptive = adaptive_max_open_value(component)
    pol = load_session_policy(component)
    mode = str(pol.get("mode") or "normal")
    eff = pol.get("max_open_effective")

    if eff is not None:
        try:
            return max(1, min(adaptive, int(eff)))
        except (TypeError, ValueError):
            pass

    if mode == "normal":
        return adaptive

    reductions = {"critical": 3, "churn": 2, "tight": 1}
    red = reductions.get(mode, 0)
    if red:
        return max(2, adaptive - red)
    return adaptive


def effective_max_l1_gross(component: str) -> int:
    cfg = _config(component)
    base = int(cfg.max_l1_gross_long())
    pol = load_session_policy(component)
    eff = pol.get("max_l1_gross_effective")
    if eff is None:
        return base
    try:
        return max(1, min(base, int(eff)))
    except (TypeError, ValueError):
        return base


def session_entry_boosts(component: str) -> dict[str, float | bool]:
    pol = load_session_policy(component)
    return {
        "enter_long_delta_boost": float(pol.get("enter_long_delta_boost") or 0),
        "enter_short_delta_boost": float(pol.get("enter_short_delta_boost") or 0),
        "pause_entries": bool(pol.get("pause_new_entries")),
    }


def session_cycle_interval_mult(component: str) -> float:
    pol = load_session_policy(component)
    try:
        return max(1.0, min(2.0, float(pol.get("cycle_interval_mult") or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def session_halt_new_entries(component: str) -> tuple[bool, str | None]:
    pol = load_session_policy(component)
    if pol.get("pause_new_entries"):
        return True, f"swarm_session_si:{pol.get('mode') or 'pause'}"
    return False, None
