"""SI participation policy — true SI acts on strong-tape / deep-lag situations.

Strategies (never loosens pre_trade_gate or MR rails):
1. Deep lag (alpha << deep floor on strong tape): wait + denylist audit; no tape override.
2. Alpha recovered: selective denylist thaw of high-rolling-expectancy universe names.
3. Strong tape, 0 exits, MR not deep-blocking: infra soft entry path (slightly lower enter threshold).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from utils.system_time import now, now_iso

log = logging.getLogger(__name__)

_MARKER_DEEP = "si_deep_lag_wait"
_MARKER_DENY = "si_denylist_audit"
_MARKER_THAW = "si_denylist_thaw"
_MARKER_INFRA = "si_infra_strong_tape_soft"


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _policy_path() -> Path:
    return _data_dir() / "si_capability" / "participation_policy.json"


def _enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_PARTICIPATION_POLICY", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _load_policy() -> dict[str, Any]:
    p = _policy_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_policy(doc: dict[str, Any]) -> None:
    p = _policy_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _portfolio() -> dict[str, Any]:
    try:
        from utils.market_benchmark import build_portfolio_session_metrics

        return build_portfolio_session_metrics() or {}
    except Exception:
        return {}


def _deep_floor() -> float:
    try:
        from utils.portfolio_session.constructive_tape_override import deep_alpha_floor

        return float(deep_alpha_floor())
    except Exception:
        return -1.0


def _soft_alpha() -> float:
    try:
        from utils.portfolio_session.constructive_tape_override import soft_alpha_threshold

        return float(soft_alpha_threshold())
    except Exception:
        return -0.8


def audit_denylist_vs_universe() -> dict[str, Any]:
    """Compare skim/infra denylists to active universes; rank thaw candidates by expectancy."""
    from utils import skim_swarm_config as skim_cfg
    from utils import infra_swarm_config as infra_cfg

    today = now().date().isoformat()
    skim_deny = set(skim_cfg.runtime_denylist())
    infra_deny = set(infra_cfg.runtime_denylist())
    try:
        skim_uni = {skim_cfg.normalize_symbol(x) for x in skim_cfg.universe()}
    except Exception:
        skim_uni = set()
    try:
        infra_uni = {infra_cfg.normalize_symbol(x) for x in infra_cfg.universe()}
    except Exception:
        infra_uni = set()

    skim_overlap = sorted(skim_deny & skim_uni)
    infra_overlap = sorted(infra_deny & infra_uni)

    candidates: list[dict[str, Any]] = []
    for component, symbols in (("skim_swarm", skim_overlap), ("infra_swarm", infra_overlap)):
        learned_dir = _data_dir() / component / "learned"
        for sym in symbols:
            doc: dict[str, Any] = {}
            f = learned_dir / f"{sym.lower().replace('.', '_')}.json"
            if not f.is_file():
                # try raw stem
                f = learned_dir / f"{sym}.json"
            if f.is_file():
                try:
                    doc = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    doc = {}
            # rolling-ish expectancy from session_stats / lifetime
            st = doc.get("session_stats") or {}
            lt = doc.get("lifetime") or doc.get("lifetime_stats") or {}
            exp = None
            for src in (st, lt):
                exits = int(src.get("exits") or 0)
                pnl = float(src.get("sum_pnl_usd") or 0)
                if exits > 0:
                    exp = pnl / exits
                    break
            params = doc.get("params") or {}
            if params.get("pause_entries"):
                continue  # never thaw mid-session brakes
            if exp is not None and exp > 0:
                candidates.append(
                    {
                        "symbol": sym,
                        "component": component,
                        "expectancy_usd": round(exp, 4),
                        "marker": _MARKER_DENY,
                    }
                )

    candidates.sort(key=lambda x: float(x.get("expectancy_usd") or 0), reverse=True)
    out = {
        "ok": True,
        "session_date_et": today,
        "skim_denylist_count": len(skim_deny),
        "infra_denylist_count": len(infra_deny),
        "skim_blocked_in_universe": skim_overlap,
        "infra_blocked_in_universe": infra_overlap,
        "thaw_candidates": candidates[:12],
        "marker": _MARKER_DENY,
        "ts": now_iso(),
    }
    log.info(
        "%s skim_blocked=%s infra_blocked=%s candidates=%s",
        _MARKER_DENY,
        skim_overlap[:8],
        infra_overlap[:8],
        [c["symbol"] for c in candidates[:6]],
    )
    return out


def apply_deep_lag_wait_strategy(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deep alpha lag on strong tape: wait — do not force participation via override."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_DEEP}
    port = port if port is not None else _portfolio()
    alpha = port.get("alpha_vs_spy_pct")
    strong = bool(port.get("strong_tape_1d"))
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    if not strong or alpha_f is None or alpha_f > floor:
        return {
            "skipped": "not_deep_lag",
            "alpha_vs_spy_pct": alpha_f,
            "deep_floor": floor,
            "strong_tape_1d": strong,
            "marker": _MARKER_DEEP,
        }

    audit = audit_denylist_vs_universe()
    today = now().date().isoformat()
    policy = _load_policy()
    if str(policy.get("session_date_et") or "") != today:
        policy = {"session_date_et": today, "events": []}
    policy.update(
        {
            "strategy": "deep_lag_wait",
            "alpha_vs_spy_pct": alpha_f,
            "deep_floor": floor,
            "strong_tape_1d": True,
            "participation_shortfall_exits": port.get("participation_shortfall_exits"),
            "thaw_candidates_pending_alpha_recovery": audit.get("thaw_candidates") or [],
            "marker": _MARKER_DEEP,
            "updated_utc": now_iso(),
        }
    )
    events = list(policy.get("events") or [])
    events.append(
        {
            "ts": now_iso(),
            "strategy": "deep_lag_wait",
            "alpha": alpha_f,
            "marker": _MARKER_DEEP,
        }
    )
    policy["events"] = events[-20:]
    _save_policy(policy)

    # Tighten swarms slightly while waiting (not override).
    session_notes: dict[str, Any] = {}
    for component in ("skim_swarm", "infra_swarm"):
        try:
            from utils.swarm_session_si import load_session_policy, save_session_policy

            pol = load_session_policy(component)
            # Soft tighten: do not pause all entries; raise long bar slightly.
            boost = float(pol.get("enter_long_delta_boost") or 0)
            pol["enter_long_delta_boost"] = round(min(0.06, max(boost, 0.02)), 4)
            pol["si_deep_lag_wait"] = True
            pol["notes"] = list(pol.get("notes") or [])[-6:] + [
                f"{_MARKER_DEEP} alpha={alpha_f:.3f}<{floor}"
            ]
            save_session_policy(component, pol)
            session_notes[component] = {"enter_long_delta_boost": pol["enter_long_delta_boost"]}
        except Exception as e:
            session_notes[component] = {"error": str(e)[:80]}

    try:
        from utils.si_capability_review import collect_metrics
        from utils.si_intervention_log import record_intervention

        record_intervention(
            component="portfolio_session",
            action="deep_lag_wait",
            metrics_snapshot=collect_metrics(),
            detail={
                "marker": _MARKER_DEEP,
                "alpha_vs_spy_pct": alpha_f,
                "deep_floor": floor,
                "audit": {
                    "skim_blocked": audit.get("skim_blocked_in_universe"),
                    "infra_blocked": audit.get("infra_blocked_in_universe"),
                    "candidates": audit.get("thaw_candidates"),
                },
                "session_notes": session_notes,
            },
            scoreable=True,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "strategy": "deep_lag_wait",
        "alpha_vs_spy_pct": alpha_f,
        "deep_floor": floor,
        "audit": audit,
        "session_notes": session_notes,
        "marker": _MARKER_DEEP,
    }


def maybe_thaw_denylist_on_recovery(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """When alpha recovers above soft threshold, thaw top positive-expectancy denylist names."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_THAW}
    if str(os.environ.get("FORTRESS_SI_DENYLIST_THAW", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "thaw_disabled", "marker": _MARKER_THAW}

    port = port if port is not None else _portfolio()
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    soft = _soft_alpha()
    if alpha_f is None or alpha_f < soft:
        return {
            "skipped": "alpha_not_recovered",
            "alpha_vs_spy_pct": alpha_f,
            "soft_threshold": soft,
            "marker": _MARKER_THAW,
        }

    try:
        max_thaw = max(0, int(os.environ.get("FORTRESS_SI_DENYLIST_THAW_MAX", "2") or 2))
    except ValueError:
        max_thaw = 2

    audit = audit_denylist_vs_universe()
    candidates = list(audit.get("thaw_candidates") or [])[:max_thaw]
    if not candidates:
        return {"ok": True, "thawed": [], "marker": _MARKER_THAW, "note": "no_candidates"}

    thawed: list[str] = []
    for c in candidates:
        sym = str(c.get("symbol") or "")
        component = str(c.get("component") or "skim_swarm")
        if not sym:
            continue
        try:
            if component == "infra_swarm":
                from utils import infra_swarm_config as cfg
            else:
                from utils import skim_swarm_config as cfg
            # Only touch file-based denylist_symbols in runtime_overrides (not env hard denylist).
            path = cfg._swarm_data_dir_path() / "runtime_overrides.json"
            if not path.is_file():
                continue
            ov = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(ov, dict):
                continue
            raw = list(ov.get("denylist_symbols") or [])
            new_list = [
                x
                for x in raw
                if cfg.normalize_symbol(str(x)) != cfg.normalize_symbol(sym)
            ]
            if len(new_list) == len(raw):
                # may be pause_symbols rather than denylist
                review = ov.get("review_actions") if isinstance(ov.get("review_actions"), dict) else {}
                pause = list(review.get("pause_symbols") or ov.get("pause_symbols") or [])
                new_pause = [
                    x
                    for x in pause
                    if cfg.normalize_symbol(str(x)) != cfg.normalize_symbol(sym)
                ]
                if len(new_pause) == len(pause):
                    continue
                if "review_actions" in ov and isinstance(ov["review_actions"], dict):
                    ov["review_actions"]["pause_symbols"] = new_pause
                else:
                    ov["pause_symbols"] = new_pause
            else:
                ov["denylist_symbols"] = new_list
            ov["si_denylist_thaw"] = {
                "ts": now_iso(),
                "symbol": sym,
                "marker": _MARKER_THAW,
                "alpha_vs_spy_pct": alpha_f,
            }
            path.write_text(json.dumps(ov, indent=2), encoding="utf-8")
            thawed.append(f"{component}:{sym}")
        except Exception as e:
            log.warning("denylist_thaw_failed %s: %s", sym, e)

    if thawed:
        try:
            from utils.si_capability_review import collect_metrics
            from utils.si_intervention_log import record_intervention

            record_intervention(
                component="portfolio_session",
                action="denylist_thaw",
                metrics_snapshot=collect_metrics(),
                detail={"marker": _MARKER_THAW, "thawed": thawed, "alpha_vs_spy_pct": alpha_f},
                scoreable=True,
            )
        except Exception:
            pass

    return {"ok": True, "thawed": thawed, "alpha_vs_spy_pct": alpha_f, "marker": _MARKER_THAW}


def apply_infra_strong_tape_soft_path(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """Strong-tape day, 0 exits, not deep lag: slightly ease infra entry thresholds."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_INFRA}
    if str(os.environ.get("FORTRESS_SI_INFRA_STRONG_TAPE_SOFT", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "soft_path_disabled", "marker": _MARKER_INFRA}

    port = port if port is not None else _portfolio()
    strong = bool(port.get("strong_tape_1d"))
    exits = int(port.get("session_exit_count") or 0)
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    if not strong or exits > 0:
        return {
            "skipped": "not_idle_strong_tape",
            "strong_tape_1d": strong,
            "session_exit_count": exits,
            "marker": _MARKER_INFRA,
        }
    if alpha_f is not None and alpha_f <= floor:
        return {
            "skipped": "deep_lag_blocks_soft_path",
            "alpha_vs_spy_pct": alpha_f,
            "marker": _MARKER_INFRA,
        }

    try:
        ease = float(os.environ.get("FORTRESS_SI_INFRA_SOFT_ENTER_DELTA", "-0.025") or -0.025)
    except (TypeError, ValueError):
        ease = -0.025
    ease = max(-0.05, min(0.0, ease))

    from utils.swarm_session_si import load_session_policy, save_session_policy

    pol = load_session_policy("infra_swarm")
    prev = float(pol.get("enter_long_delta_boost") or 0)
    # Ease entry: lower effective enter_long (negative boost). Don't fight deep-lag tighten.
    if pol.get("si_deep_lag_wait"):
        return {"skipped": "deep_lag_wait_active", "marker": _MARKER_INFRA}
    new_boost = round(min(prev, ease), 4)
    pol["enter_long_delta_boost"] = new_boost
    pol["enter_short_delta_boost"] = round(max(float(pol.get("enter_short_delta_boost") or 0), 0.0), 4)
    pol["si_infra_strong_tape_soft"] = True
    notes = list(pol.get("notes") or [])
    notes.append(f"{_MARKER_INFRA} enter_long_delta_boost={new_boost}")
    pol["notes"] = notes[-8:]
    save_session_policy("infra_swarm", pol)

    try:
        from utils.si_capability_review import collect_metrics
        from utils.si_intervention_log import record_intervention

        record_intervention(
            component="infra_swarm",
            action="infra_strong_tape_soft",
            metrics_snapshot=collect_metrics(),
            detail={
                "marker": _MARKER_INFRA,
                "enter_long_delta_boost": new_boost,
                "alpha_vs_spy_pct": alpha_f,
            },
            scoreable=True,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "enter_long_delta_boost": new_boost,
        "alpha_vs_spy_pct": alpha_f,
        "marker": _MARKER_INFRA,
    }


def run_participation_si_cycle(*, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Orchestrate deep-lag / denylist thaw / infra soft path for RTH SI."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled"}
    port = (metrics or {}).get("portfolio_session") if metrics else None
    if not isinstance(port, dict) or not port:
        port = _portfolio()

    out: dict[str, Any] = {"ok": True, "marker": "si_participation_cycle"}
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    soft = _soft_alpha()

    if bool(port.get("strong_tape_1d")) and alpha_f is not None and alpha_f <= floor:
        out["deep_lag"] = apply_deep_lag_wait_strategy(port=port)
        out["denylist_audit"] = out["deep_lag"].get("audit") if isinstance(out["deep_lag"], dict) else None
    else:
        out["deep_lag"] = {"skipped": "not_deep_lag"}
        # Always useful on strong tape: denylist audit trail
        if bool(port.get("strong_tape_1d")):
            out["denylist_audit"] = audit_denylist_vs_universe()
        if alpha_f is not None and alpha_f >= soft:
            out["thaw"] = maybe_thaw_denylist_on_recovery(port=port)
        out["infra_soft"] = apply_infra_strong_tape_soft_path(port=port)

    return out


__all__ = [
    "apply_deep_lag_wait_strategy",
    "apply_infra_strong_tape_soft_path",
    "audit_denylist_vs_universe",
    "maybe_thaw_denylist_on_recovery",
    "run_participation_si_cycle",
]
