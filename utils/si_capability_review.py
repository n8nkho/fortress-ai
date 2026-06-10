"""Continuous SI capability review — measure outcomes vs objectives and tune meta-SI knobs."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.system_time import ensure_system_tz, now_iso, system_tz_name

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def _capability_dir() -> Path:
    return _data_dir() / "si_capability"


def objectives_path() -> Path:
    return _ROOT / "config" / "si_objectives.json"


def capability_registry_path() -> Path:
    return _ROOT / "config" / "si_capability_registry.json"


def overrides_path() -> Path:
    return _capability_dir() / "overrides.json"


def state_path() -> Path:
    return _capability_dir() / "state.json"


def latest_report_path() -> Path:
    return _capability_dir() / "latest.json"


def review_log_path() -> Path:
    return _capability_dir() / "review_log.jsonl"


def load_objectives() -> list[dict[str, Any]]:
    p = objectives_path()
    if not p.is_file():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        rows = doc.get("objectives") or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception:
        return []


def load_capability_registry() -> dict[str, Any]:
    p = capability_registry_path()
    if not p.is_file():
        return {"capabilities": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"capabilities": {}}
    except Exception:
        return {"capabilities": {}}


def load_overrides() -> dict[str, Any]:
    p = overrides_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def save_overrides(doc: dict[str, Any]) -> None:
    p = overrides_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.setdefault("system_tz", system_tz_name())
    doc["updated_at"] = now_iso()
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.is_file():
        return {"interventions": [], "last_metrics": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"interventions": [], "last_metrics": {}}
    except Exception:
        return {"interventions": [], "last_metrics": {}}


def save_state(doc: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.setdefault("system_tz", system_tz_name())
    doc["updated_at"] = now_iso()
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def get_capability(name: str, default: float | int | None = None) -> float | int | None:
    ov = load_overrides()
    caps = ov.get("capabilities") if isinstance(ov.get("capabilities"), dict) else {}
    if name in caps:
        return caps[name]
    reg = (load_capability_registry().get("capabilities") or {}).get(name) or {}
    if "default" in reg:
        return reg["default"]
    return default


def _swarm_decisions_path(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name / "decisions.jsonl"


def _component_session_metrics(component: str, *, window_sessions: int = 5) -> dict[str, Any]:
    from utils.swarm_decisions_pnl import per_session_totals

    dec = _swarm_decisions_path(component)
    by_day = per_session_totals(dec)
    days = sorted(by_day.keys())[-window_sessions:]
    session_rows: list[dict[str, Any]] = []
    total_pnl = 0.0
    total_exits = 0
    wins = 0
    losses = 0
    win_pnls: list[float] = []
    loss_pnls: list[float] = []

    from utils.swarm_decisions_pnl import iter_executed_exits

    for day in days:
        row = by_day[day]
        pnl = float(row.get("realized_usd") or 0)
        ex = int(row.get("exit_count") or 0)
        session_rows.append({"session_date_et": day, "realized_usd": pnl, "exit_count": ex})
        total_pnl += pnl
        total_exits += ex

    for exit_row in iter_executed_exits(dec):
        if exit_row["session_date_et"] not in days:
            continue
        p = float(exit_row["pnl_usd"])
        if p >= 0:
            wins += 1
            win_pnls.append(p)
        else:
            losses += 1
            loss_pnls.append(abs(p))

    expectancy = (total_pnl / total_exits) if total_exits else None
    avg_win = (sum(win_pnls) / len(win_pnls)) if win_pnls else 0.0
    avg_loss = (sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else None
    win_rate = (wins / total_exits) if total_exits else None

    return {
        "component": component,
        "window_sessions": window_sessions,
        "sessions": session_rows,
        "rolling_realized_usd": round(total_pnl, 4),
        "rolling_exits": total_exits,
        "rolling_expectancy_usd": round(expectancy, 4) if expectancy is not None else None,
        "rolling_payoff_ratio": round(payoff, 4) if payoff is not None else None,
        "rolling_win_rate": round(win_rate, 4) if win_rate is not None else None,
    }


def _unified_metrics(*, window_sessions: int = 10) -> dict[str, Any]:
    from utils.ai_pnl_ledger import ledger_path
    from utils.swarm_decisions_pnl import wave_session_date_et

    p = ledger_path()
    by_day: dict[str, float] = {}
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("source") or "") != "unified_ai_agent":
                continue
            day = wave_session_date_et(str(row.get("timestamp") or ""))
            if not day:
                continue
            by_day[day] = round(by_day.get(day, 0.0) + float(row.get("pnl") or 0), 4)
    days = sorted(by_day.keys())[-window_sessions:]
    total = sum(by_day.get(d, 0.0) for d in days)
    exits = len([d for d in days if by_day.get(d, 0.0) != 0.0])
    return {
        "component": "unified_ai",
        "window_sessions": window_sessions,
        "rolling_realized_usd": round(total, 4),
        "rolling_exits": exits,
        "sessions": [{"session_date_et": d, "realized_usd": by_day.get(d, 0.0)} for d in days],
    }


def collect_metrics() -> dict[str, Any]:
    from utils.classic_bridge import classic_rolling_metrics

    metrics = {
        "skim_swarm": _component_session_metrics("skim_swarm", window_sessions=5),
        "infra_swarm": _component_session_metrics("infra_swarm", window_sessions=5),
        "unified_ai": _unified_metrics(window_sessions=10),
        "classic_fortress": classic_rolling_metrics(window_sessions=10),
        "ts": now_iso(),
    }
    return metrics


def _metric_value(metrics: dict[str, Any], component: str, metric: str) -> float | None:
    if component == "si_meta" and metric == "intervention_success_rate":
        st = load_state()
        return float(st.get("intervention_success_rate")) if st.get("intervention_success_rate") is not None else None
    comp = metrics.get(component) or {}
    val = comp.get(metric)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def evaluate_objective_gaps(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for obj in load_objectives():
        comp = str(obj.get("component") or "")
        metric = str(obj.get("metric") or "")
        target_min = obj.get("target_min")
        target_max = obj.get("target_max")
        if target_min is None and target_max is None:
            continue
        min_exits = int(obj.get("min_exits") or 0)
        min_screens = int(obj.get("min_screens") or 0)
        comp_metrics = metrics.get(comp) or {}
        if comp == "classic_fortress":
            if min_screens and int(comp_metrics.get("screens_sampled") or 0) < min_screens:
                continue
            fills = int(comp_metrics.get("rolling_fills") or 0)
            if min_exits and fills < min_exits:
                continue
            # Adaptive recency: use capability knob as effective target_max when set.
            if str(obj.get("metric") or "") == "days_since_last_fill":
                try:
                    from utils.si_adaptive_actions import adaptive_classic_fill_recency_max

                    obj = dict(obj)
                    obj["target_max"] = adaptive_classic_fill_recency_max()
                    target_max = obj["target_max"]
                except Exception:
                    pass
        elif comp != "si_meta":
            exits = int(comp_metrics.get("rolling_exits") or 0)
            if exits < min_exits:
                continue
        val = _metric_value(metrics, comp, metric)
        if val is None:
            continue
        if target_max is not None:
            if float(val) > float(target_max):
                gaps.append(
                    {
                        "objective_id": obj.get("id"),
                        "component": comp,
                        "metric": metric,
                        "value": round(float(val), 4),
                        "target_max": float(target_max),
                        "gap": round(float(val) - float(target_max), 4),
                        "priority": str(obj.get("priority") or "medium"),
                        "description": obj.get("description"),
                    }
                )
            continue
        if target_min is not None and float(val) < float(target_min):
            gaps.append(
                {
                    "objective_id": obj.get("id"),
                    "component": comp,
                    "metric": metric,
                    "value": round(float(val), 4),
                    "target_min": float(target_min),
                    "gap": round(float(target_min) - float(val), 4),
                    "priority": str(obj.get("priority") or "medium"),
                    "description": obj.get("description"),
                }
            )
    gaps.sort(key=lambda g: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(g["priority"], 9))
    return gaps


def _clamp_capability(name: str, value: float) -> float:
    reg = (load_capability_registry().get("capabilities") or {}).get(name) or {}
    bounds = reg.get("bounds") or {}
    lo = float(bounds.get("min", value))
    hi = float(bounds.get("max", value))
    return max(lo, min(hi, float(value)))


def propose_capability_updates(
    metrics: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bounded meta-SI proposals from objective gaps."""
    proposals: list[dict[str, Any]] = []
    skim = metrics.get("skim_swarm") or {}
    skim_exp = skim.get("rolling_expectancy_usd")
    skim_payoff = skim.get("rolling_payoff_ratio")

    if any(g.get("objective_id") == "skim_session_expectancy" for g in gaps):
        cur_target = float(get_capability("winning_pattern_share_target", 0.75) or 0.75)
        from utils.skim_pattern_review import swarm_winning_pattern_share

        share = swarm_winning_pattern_share(min_exits=3)
        if share is not None and share < cur_target:
            new_target = _clamp_capability("winning_pattern_share_target", max(0.4, float(share) + 0.05))
            if new_target < cur_target - 0.02:
                proposals.append(
                    {
                        "capability": "winning_pattern_share_target",
                        "current": cur_target,
                        "proposed": round(new_target, 4),
                        "reason": "Skim expectancy gap — align pattern-share target with measured portfolio.",
                    }
                )
        cur_cap = float(get_capability("edge_autofix_rr_boost_cap", 0.2) or 0.2)
        if skim_payoff is not None and float(skim_payoff) < 1.0:
            new_cap = _clamp_capability("edge_autofix_rr_boost_cap", min(0.25, cur_cap + 0.02))
            if new_cap > cur_cap + 0.009:
                proposals.append(
                    {
                        "capability": "edge_autofix_rr_boost_cap",
                        "current": cur_cap,
                        "proposed": round(new_cap, 4),
                        "reason": "Inverted payoff — allow stronger RR session boost cap.",
                    }
                )
        cur_min = float(get_capability("edge_autofix_min_exits", 4) or 4)
        if int(skim.get("rolling_exits") or 0) < 8:
            new_min = _clamp_capability("edge_autofix_min_exits", min(12, cur_min + 1))
            if new_min > cur_min:
                proposals.append(
                    {
                        "capability": "edge_autofix_min_exits",
                        "current": cur_min,
                        "proposed": int(new_min),
                        "reason": "Low sample — require more exits before edge autofix.",
                    }
                )

    if gaps:
        cur_cadence = float(get_capability("rth_review_cadence_mult", 1.0) or 1.0)
        new_cadence = _clamp_capability("rth_review_cadence_mult", max(0.5, cur_cadence - 0.1))
        if new_cadence < cur_cadence - 0.04:
            proposals.append(
                {
                    "capability": "rth_review_cadence_mult",
                    "current": cur_cadence,
                    "proposed": round(new_cadence, 4),
                    "reason": "Objective gaps open — increase review cadence.",
                }
            )

    if not gaps:
        cur_cadence = float(get_capability("rth_review_cadence_mult", 1.0) or 1.0)
        if cur_cadence < 0.99:
            proposals.append(
                {
                    "capability": "rth_review_cadence_mult",
                    "current": cur_cadence,
                    "proposed": round(_clamp_capability("rth_review_cadence_mult", min(1.0, cur_cadence + 0.05)), 4),
                    "reason": "Objectives met — relax review cadence toward default.",
                }
            )
        return proposals

    # When primary knobs are saturated, adapt secondary knobs from gap severity.
    gap_sev = sum(min(1.0, float(g.get("gap") or 0)) for g in gaps) / max(len(gaps), 1)
    if any(g.get("objective_id") == "skim_session_expectancy" for g in gaps):
        cur_strength = float(get_capability("rolling_edge_autofix_strength", 0.55) or 0.55)
        new_strength = _clamp_capability("rolling_edge_autofix_strength", min(1.0, cur_strength + 0.05 * gap_sev))
        if new_strength > cur_strength + 0.02:
            proposals.append(
                {
                    "capability": "rolling_edge_autofix_strength",
                    "current": cur_strength,
                    "proposed": round(new_strength, 4),
                    "reason": "Primary knobs saturated — increase rolling-aware autofix strength.",
                }
            )
    if any(g.get("objective_id") == "classic_fill_recency" for g in gaps):
        cur_days = float(get_capability("classic_fill_recency_days_max", 7.0) or 7.0)
        new_days = _clamp_capability("classic_fill_recency_days_max", max(3.0, cur_days - 1.0 * gap_sev))
        if new_days < cur_days - 0.4:
            proposals.append(
                {
                    "capability": "classic_fill_recency_days_max",
                    "current": cur_days,
                    "proposed": round(new_days, 2),
                    "reason": "Classic fill recency gap — tighten adaptive day threshold.",
                }
            )
    if any(g.get("component") == "unified_ai" for g in gaps) or gap_sev > 0.5:
        cur_trim = float(get_capability("unified_loser_trim_pct_equity", 0.05) or 0.05)
        new_trim = _clamp_capability("unified_loser_trim_pct_equity", max(0.02, cur_trim - 0.005 * gap_sev))
        if new_trim < cur_trim - 0.001:
            proposals.append(
                {
                    "capability": "unified_loser_trim_pct_equity",
                    "current": cur_trim,
                    "proposed": round(new_trim, 4),
                    "reason": "Portfolio drag — lower adaptive trim threshold (earlier action).",
                }
            )

    return proposals


def propose_classic_recommendations(
    metrics: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Action hints for Classic sibling (human/evolve path — not auto-applied from fortress-ai)."""
    classic = metrics.get("classic_fortress") or {}
    recs: list[dict[str, Any]] = []
    gap_ids = {g.get("objective_id") for g in gaps}

    if "classic_candidate_throughput" in gap_ids:
        regime = classic.get("latest_regime") or "unknown"
        recs.append(
            {
                "component": "classic_fortress",
                "action": "screener_bull_rsi_review",
                "detail": (
                    f"Regime {regime}: verify FORTRESS_SCREENER_BULL_RSI_T1 (default 62) and "
                    "run orchestrator.py evolve on trading-bot."
                ),
            }
        )
    if "classic_fill_activity" in gap_ids:
        days = classic.get("days_since_last_fill")
        recs.append(
            {
                "component": "classic_fortress",
                "action": "restore_fill_pipeline",
                "detail": (
                    f"No fills in window; last fill {days}d ago — check cron screen/monitor, "
                    "entry gate, and TRENDING_BULL prefilter rejects."
                ),
            }
        )
    if "classic_fill_recency" in gap_ids:
        days = classic.get("days_since_last_fill")
        regime = classic.get("latest_regime") or "unknown"
        recs.append(
            {
                "component": "classic_fortress",
                "action": "adaptive_fill_recency",
                "detail": (
                    f"Last fill {days}d ago (regime {regime}) — run trading-bot screen/evolve; "
                    "tighten classic_fill_recency_days_max if gap persists."
                ),
            }
        )
    if "classic_session_expectancy" in gap_ids:
        recs.append(
            {
                "component": "classic_fortress",
                "action": "classic_param_tune",
                "detail": "Negative classic expectancy — run orchestrator tune / recursive_evolution.",
            }
        )
    return recs


def apply_capability_updates(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not proposals:
        return []
    ov = load_overrides()
    caps = ov.setdefault("capabilities", {})
    applied: list[dict[str, Any]] = []
    for prop in proposals:
        name = str(prop.get("capability") or "")
        if not name:
            continue
        proposed = prop.get("proposed")
        if proposed is None:
            continue
        if isinstance(proposed, float):
            proposed = round(_clamp_capability(name, proposed), 4)
        else:
            proposed = int(_clamp_capability(name, float(proposed)))
        caps[name] = proposed
        applied.append({**prop, "applied": proposed})
    if applied:
        ov["capabilities"] = caps
        save_overrides(ov)
    return applied


def _update_intervention_effectiveness(
    metrics: dict[str, Any],
    applied: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Track whether prior capability changes correlated with metric improvement."""
    last = state.get("last_metrics") if isinstance(state.get("last_metrics"), dict) else {}
    interventions = list(state.get("interventions") or [])[-40:]

    if applied:
        interventions.append({"ts": now_iso(), "applied": applied, "metrics_snapshot": metrics})

    improved = 0
    scored = 0
    for comp in ("skim_swarm", "infra_swarm", "classic_fortress"):
        cur_exp = (metrics.get(comp) or {}).get("rolling_expectancy_usd")
        prev_exp = (last.get(comp) or {}).get("rolling_expectancy_usd")
        if cur_exp is None or prev_exp is None:
            continue
        scored += 1
        if float(cur_exp) > float(prev_exp):
            improved += 1

    rate = (improved / scored) if scored else None
    state["intervention_success_rate"] = round(rate, 4) if rate is not None else state.get("intervention_success_rate")
    state["interventions"] = interventions
    state["last_metrics"] = metrics
    return state


def effective_rth_interval_sec() -> int:
    from utils.rth_autonomous_si import rth_cycle_interval_sec

    base = rth_cycle_interval_sec()
    mult = float(get_capability("rth_review_cadence_mult", 1.0) or 1.0)
    return max(300, int(base * mult))


def effective_edge_autofix_min_exits() -> int:
    return int(get_capability("edge_autofix_min_exits", 4) or 4)


def effective_edge_autofix_rr_boost_cap() -> float:
    return float(get_capability("edge_autofix_rr_boost_cap", 0.2) or 0.2)


def _append_review_log(doc: dict[str, Any]) -> None:
    p = review_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(doc, default=str) + "\n")


def _upsert_capability_queue_findings(gaps: list[dict[str, Any]], applied: list[dict[str, Any]]) -> None:
    if not gaps and not applied:
        return
    try:
        from utils.si_recommendation_queue import upsert_from_finding

        for gap in gaps[:5]:
            upsert_from_finding(
                {
                    "code": "si_objective_gap",
                    "severity": "high" if gap.get("priority") == "critical" else "medium",
                    "component": gap.get("component"),
                    "objective_id": gap.get("objective_id"),
                    "metric": gap.get("metric"),
                    "value": gap.get("value"),
                    "target_min": gap.get("target_min"),
                    "recommendation": (
                        f"Objective {gap.get('objective_id')} missed: "
                        f"{gap.get('metric')}={gap.get('value')} < {gap.get('target_min')}."
                    ),
                    "si_action": "si_capability_review",
                },
                source="capability_review",
            )
        if applied:
            upsert_from_finding(
                {
                    "code": "si_capability_auto_applied",
                    "severity": "info",
                    "component": "si_meta",
                    "applied_count": len(applied),
                    "recommendation": "Meta-SI knobs updated — see data/si_capability/overrides.json.",
                    "si_action": "monitor",
                }
            )
    except Exception:
        pass


def run_capability_review_cycle(*, apply: bool = True) -> dict[str, Any]:
    """Full continuous review: measure → gap → propose → apply → persist."""
    metrics = collect_metrics()
    gaps = evaluate_objective_gaps(metrics)
    proposals = propose_capability_updates(metrics, gaps)
    classic_recs = propose_classic_recommendations(metrics, gaps)
    applied: list[dict[str, Any]] = apply_capability_updates(proposals) if apply else []

    try:
        from utils.classic_bridge import push_findings_to_classic_queue

        push_findings_to_classic_queue(gaps, classic_recs)
    except Exception:
        pass

    state = load_state()
    state = _update_intervention_effectiveness(metrics, applied, state)
    save_state(state)

    singularity: dict[str, Any] = {}
    try:
        from utils.si_singularity import run_singularity_cycle

        singularity = run_singularity_cycle(metrics, gaps, apply=apply)
        if singularity.get("singularity_applied"):
            applied = applied + list(singularity.get("singularity_applied") or [])
    except Exception as e:
        singularity = {"error": str(e)[:120]}

    report = {
        "ok": True,
        "ts": now_iso(),
        "system_tz": system_tz_name(),
        "metrics": metrics,
        "objective_gaps": gaps,
        "classic_recommendations": classic_recs,
        "proposals": proposals,
        "applied": applied,
        "singularity": singularity,
        "intervention_success_rate": state.get("intervention_success_rate"),
        "effective_rth_interval_sec": effective_rth_interval_sec(),
        "overrides": load_overrides().get("capabilities") or {},
    }

    _capability_dir().mkdir(parents=True, exist_ok=True)
    latest_report_path().write_text(json.dumps(report, indent=2), encoding="utf-8")
    _append_review_log(
        {
            "ts": report["ts"],
            "gaps": len(gaps),
            "applied": len(applied),
            "intervention_success_rate": state.get("intervention_success_rate"),
            "singularity_phase": singularity.get("phase"),
        }
    )
    if apply:
        _upsert_capability_queue_findings(gaps, applied)
        try:
            from utils.si_recommendation_queue import reconcile_cleared_findings

            active = [
                {
                    "code": "si_objective_gap",
                    "component": gap.get("component"),
                    "objective_id": gap.get("objective_id"),
                }
                for gap in gaps
            ]
            if applied:
                active.append({"code": "si_capability_auto_applied", "component": "si_meta"})
            reconcile_cleared_findings({}, active_findings=active)
        except Exception:
            pass
    return report
