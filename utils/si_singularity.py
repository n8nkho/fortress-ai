"""
SI Singularity — surpass floor objectives via dynamic aspire targets and portfolio optimization.

Phases:
- deficit: any floor objective missed → maximize gap closure (existing capability review)
- baseline: all floors met, no aspire pressure
- surpass: floors met, aspire targets not yet reached → escalate SI aggression
- singularity: all aspire met → auto-lift aspire targets (bounded) and compound
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.system_time import ensure_system_tz, now_iso

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def config_path() -> Path:
    return _ROOT / "config" / "si_singularity.json"


def state_path() -> Path:
    return _data_dir() / "si_singularity" / "state.json"


def latest_path() -> Path:
    return _data_dir() / "si_singularity" / "latest.json"


def singularity_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_SINGULARITY", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def load_config() -> dict[str, Any]:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {"aspire_overrides": {}, "singularity_streak": 0}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"aspire_overrides": {}, "singularity_streak": 0}
    except Exception:
        return {"aspire_overrides": {}, "singularity_streak": 0}


def save_state(doc: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_utc"] = now_iso()
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def effective_aspire(objective_id: str, obj: dict[str, Any]) -> float | None:
    """Dynamic aspire target: config aspire_defaults + objective target_aspire + runtime lifts."""
    cfg = load_config()
    defaults = cfg.get("aspire_defaults") or {}
    base = obj.get("target_aspire")
    if base is None and objective_id in defaults:
        base = defaults[objective_id]
    if base is None:
        return None
    ov = (load_state().get("aspire_overrides") or {}).get(objective_id)
    if ov is not None:
        return float(ov)
    return float(base)


def combined_portfolio_realized(metrics: dict[str, Any]) -> float:
    skim = float((metrics.get("skim_swarm") or {}).get("rolling_realized_usd") or 0)
    infra = float((metrics.get("infra_swarm") or {}).get("rolling_realized_usd") or 0)
    classic = float((metrics.get("classic_fortress") or {}).get("rolling_realized_usd") or 0)
    unified = float((metrics.get("unified_ai") or {}).get("rolling_realized_usd") or 0)
    return round(skim + infra + classic + unified, 4)


def evaluate_surpass_gaps(
    metrics: dict[str, Any],
    floor_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Objectives at floor but below aspire — room to surpass."""
    if floor_gaps:
        return []
    from utils.si_capability_review import _metric_value, load_objectives

    gap_ids = {g.get("objective_id") for g in floor_gaps}
    surpass: list[dict[str, Any]] = []
    for obj in load_objectives():
        oid = str(obj.get("id") or "")
        if oid in gap_ids:
            continue
        comp = str(obj.get("component") or "")
        metric = str(obj.get("metric") or "")
        aspire = effective_aspire(oid, obj)
        if aspire is None:
            continue
        val = _metric_value(metrics, comp, metric)
        if val is None:
            continue
        target_min = obj.get("target_min")
        target_max = obj.get("target_max")
        if target_max is not None:
            # lower is better (e.g. days_since_last_fill)
            floor = float(obj.get("target_max") or aspire)
            if float(val) <= floor and float(val) > float(aspire):
                surpass.append(
                    {
                        "objective_id": oid,
                        "component": comp,
                        "metric": metric,
                        "value": round(float(val), 4),
                        "target_aspire": float(aspire),
                        "target_floor": floor,
                        "gap": round(float(val) - float(aspire), 4),
                        "kind": "surpass_max",
                        "priority": str(obj.get("priority") or "medium"),
                    }
                )
            continue
        if target_min is not None and float(val) >= float(target_min) and float(val) < float(aspire):
            surpass.append(
                {
                    "objective_id": oid,
                    "component": comp,
                    "metric": metric,
                    "value": round(float(val), 4),
                    "target_aspire": float(aspire),
                    "target_floor": float(target_min),
                    "gap": round(float(aspire) - float(val), 4),
                    "kind": "surpass_min",
                    "priority": str(obj.get("priority") or "medium"),
                }
            )

    cfg = load_config()
    pf = cfg.get("portfolio") or {}
    pf_min = float(pf.get("target_min") or 0)
    pf_aspire = float(pf.get("target_aspire") or pf_min * 2)
    combined = combined_portfolio_realized(metrics)
    if combined >= pf_min and combined < pf_aspire:
        surpass.append(
            {
                "objective_id": "portfolio_combined_pnl",
                "component": "portfolio",
                "metric": "combined_rolling_realized_usd",
                "value": combined,
                "target_aspire": pf_aspire,
                "target_floor": pf_min,
                "gap": round(pf_aspire - combined, 4),
                "kind": "surpass_min",
                "priority": "critical",
            }
        )
    surpass.sort(key=lambda g: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(g["priority"], 9))
    return surpass


def compute_phase(
    floor_gaps: list[dict[str, Any]],
    surpass_gaps: list[dict[str, Any]],
) -> str:
    if floor_gaps:
        return "deficit"
    if not surpass_gaps:
        return "singularity"
    return "surpass"


def compute_surpass_rate(
    metrics: dict[str, Any],
    floor_gaps: list[dict[str, Any]],
    surpass_gaps: list[dict[str, Any]],
) -> float:
    from utils.si_capability_review import load_objectives

    if floor_gaps:
        return 0.0
    total = 0
    met_aspire = 0
    for obj in load_objectives():
        oid = str(obj.get("id") or "")
        aspire = effective_aspire(oid, obj)
        if aspire is None:
            continue
        comp = str(obj.get("component") or "")
        metric = str(obj.get("metric") or "")
        from utils.si_capability_review import _metric_value

        val = _metric_value(metrics, comp, metric)
        if val is None:
            continue
        total += 1
        if obj.get("target_max") is not None:
            if float(val) <= float(aspire):
                met_aspire += 1
        elif float(val) >= float(aspire):
            met_aspire += 1
    cfg = load_config()
    pf = cfg.get("portfolio") or {}
    if pf.get("target_aspire"):
        total += 1
        combined = combined_portfolio_realized(metrics)
        if combined >= float(pf.get("target_aspire") or 0):
            met_aspire += 1
    if not surpass_gaps and total > 0:
        return 1.0
    if total == 0:
        return 1.0 if not floor_gaps else 0.0
    return round(met_aspire / total, 4)


def propose_singularity_capability_updates(
    phase: str,
    surpass_gaps: list[dict[str, Any]],
    *,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    from utils.si_capability_review import get_capability, _clamp_capability

    proposals: list[dict[str, Any]] = []
    agg = float(get_capability("singularity_aggression_mult", 1.0) or 1.0)

    if phase == "deficit":
        return proposals

    if phase in ("surpass", "singularity"):
        cur_strength = float(get_capability("rolling_edge_autofix_strength", 0.55) or 0.55)
        lift = 0.04 * agg * (1.0 + len(surpass_gaps) * 0.1)
        new_strength = _clamp_capability("rolling_edge_autofix_strength", min(1.0, cur_strength + lift))
        if new_strength > cur_strength + 0.02:
            proposals.append(
                {
                    "capability": "rolling_edge_autofix_strength",
                    "current": cur_strength,
                    "proposed": round(new_strength, 4),
                    "reason": f"Singularity {phase} — push beyond floor objectives.",
                }
            )

        cur_cadence = float(get_capability("rth_review_cadence_mult", 1.0) or 1.0)
        if phase == "surpass" and cur_cadence > 0.55:
            new_cadence = _clamp_capability("rth_review_cadence_mult", max(0.5, cur_cadence - 0.05 * agg))
            if new_cadence < cur_cadence - 0.03:
                proposals.append(
                    {
                        "capability": "rth_review_cadence_mult",
                        "current": cur_cadence,
                        "proposed": round(new_cadence, 4),
                        "reason": "Surpass mode — increase SI review frequency to close aspire gaps.",
                    }
                )

        if phase == "singularity":
            cur_agg = float(get_capability("singularity_aggression_mult", 1.0) or 1.0)
            new_agg = _clamp_capability("singularity_aggression_mult", min(1.5, cur_agg + 0.05))
            if new_agg > cur_agg + 0.02:
                proposals.append(
                    {
                        "capability": "singularity_aggression_mult",
                        "current": cur_agg,
                        "proposed": round(new_agg, 4),
                        "reason": "Singularity phase — all aspire met; compound aggression.",
                    }
                )

        # Portfolio-weighted: boost component with largest aspire gap
        if surpass_gaps and metrics:
            top = surpass_gaps[0]
            comp = str(top.get("component") or "")
            if comp == "skim_swarm":
                cur = float(get_capability("winning_pattern_share_target", 0.75) or 0.75)
                new = _clamp_capability("winning_pattern_share_target", min(0.85, cur + 0.03 * agg))
                if new > cur + 0.01:
                    proposals.append(
                        {
                            "capability": "winning_pattern_share_target",
                            "current": cur,
                            "proposed": round(new, 4),
                            "reason": "Surpass skim — raise pattern-share aspire.",
                        }
                    )

    return proposals


def maybe_lift_aspire_targets(phase: str, surpass_rate: float) -> dict[str, Any]:
    """When sustained singularity, raise dynamic aspire targets (bounded)."""
    cfg = load_config()
    thresholds = cfg.get("phase_thresholds") or {}
    need_rate = float(thresholds.get("singularity_surpass_rate") or 0.85)
    need_cycles = int(thresholds.get("singularity_cycles_to_lift") or 3)
    lift_pct = float(
        __import__("utils.si_capability_review", fromlist=["get_capability"]).get_capability(
            "singularity_aspire_lift_pct", 0.05
        )
        or 0.05
    )

    state = load_state()
    streak = int(state.get("singularity_streak") or 0)
    if phase == "singularity" and surpass_rate >= need_rate:
        streak += 1
    else:
        streak = 0
    state["singularity_streak"] = streak
    state["last_phase"] = phase
    state["last_surpass_rate"] = surpass_rate

    lifted: dict[str, float] = {}
    if streak >= need_cycles and phase == "singularity":
        overrides = dict(state.get("aspire_overrides") or {})
        defaults = cfg.get("aspire_defaults") or {}
        from utils.si_capability_review import load_objectives

        for obj in load_objectives():
            oid = str(obj.get("id") or "")
            cur = effective_aspire(oid, obj)
            if cur is None:
                continue
            if obj.get("target_max") is not None:
                new = max(1.0, float(cur) * (1.0 - lift_pct))
            else:
                new = float(cur) * (1.0 + lift_pct)
            overrides[oid] = round(new, 4)
            lifted[oid] = overrides[oid]
        state["aspire_overrides"] = overrides
        state["singularity_streak"] = 0
        state["last_aspire_lift_utc"] = now_iso()

    save_state(state)
    return {"lifted": lifted, "streak": streak, "phase": phase}


def singularity_directives(phase: str, surpass_gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Action directives for adaptive SI + Classic bridge."""
    directives: list[dict[str, Any]] = []
    if phase == "deficit":
        return directives
    for g in surpass_gaps[:5]:
        directives.append(
            {
                "objective_id": g.get("objective_id"),
                "component": g.get("component"),
                "action": "surpass_escalate",
                "detail": (
                    f"Surpass {g.get('objective_id')}: {g.get('metric')}={g.get('value')} "
                    f"→ aspire {g.get('target_aspire')} (floor {g.get('target_floor')})."
                ),
            }
        )
    if phase == "singularity":
        directives.append(
            {
                "component": "si_meta",
                "action": "singularity_compound",
                "detail": "All aspire targets met — auto-lift aspire and compound SI aggression.",
            }
        )
    return directives


def push_surpass_to_classic(directives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    classic_dirs = [d for d in directives if str(d.get("component") or "") == "classic_fortress"]
    if not classic_dirs:
        return []
    try:
        from utils.classic_bridge import push_findings_to_classic_queue

        gaps = [
            {
                "component": "classic_fortress",
                "objective_id": d.get("objective_id") or "classic_surpass",
                "metric": "surpass",
                "value": 0,
                "gap": 1,
                "priority": "high",
            }
            for d in classic_dirs
        ]
        recs = [{"component": "classic_fortress", "action": d.get("action"), "detail": d.get("detail")} for d in classic_dirs]
        return push_findings_to_classic_queue(gaps, recs)
    except Exception:
        return []


def run_singularity_cycle(
    metrics: dict[str, Any],
    floor_gaps: list[dict[str, Any]],
    *,
    apply: bool = True,
) -> dict[str, Any]:
    if not singularity_enabled():
        return {"ok": True, "skipped": "singularity_disabled"}

    surpass_gaps = evaluate_surpass_gaps(metrics, floor_gaps)
    phase = compute_phase(floor_gaps, surpass_gaps)
    surpass_rate = compute_surpass_rate(metrics, floor_gaps, surpass_gaps)
    combined = combined_portfolio_realized(metrics)

    proposals = propose_singularity_capability_updates(phase, surpass_gaps, metrics=metrics)
    applied: list[dict[str, Any]] = []
    if apply and proposals:
        from utils.si_capability_review import apply_capability_updates

        applied = apply_capability_updates(proposals)

    lift = maybe_lift_aspire_targets(phase, surpass_rate)
    directives = singularity_directives(phase, surpass_gaps)
    classic_pushed = push_surpass_to_classic(directives) if apply else []

    report = {
        "ok": True,
        "ts": now_iso(),
        "phase": phase,
        "mission": load_config().get("mission"),
        "combined_rolling_realized_usd": combined,
        "surpass_rate": surpass_rate,
        "floor_gaps": len(floor_gaps),
        "surpass_gaps": surpass_gaps,
        "singularity_proposals": proposals,
        "singularity_applied": applied,
        "aspire_lift": lift,
        "directives": directives,
        "classic_queue_pushed": len(classic_pushed),
    }

    latest_path().parent.mkdir(parents=True, exist_ok=True)
    latest_path().write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
