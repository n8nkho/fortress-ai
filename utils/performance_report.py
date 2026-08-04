"""Operator performance snapshot — PnL + SI effectiveness in one place.

Used by dashboards/agents when answering "how did we do today?" so SI health
is never omitted from performance reviews.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from utils.system_time import now, now_iso

_ET = ZoneInfo("America/New_York")


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _sleeve_pnl(op: dict[str, Any], sleeve: str) -> dict[str, Any]:
    block = (op.get(sleeve) or {}).get("pnl") or {}
    daily = block.get("daily") or {}
    return {
        "session_date_et": block.get("session_date_et"),
        "realized_usd": daily.get("realized_usd"),
        "exit_count": daily.get("exit_count"),
        "open_positions": daily.get("open_positions"),
        "cumulative_realized_usd": (block.get("cumulative") or {}).get("realized_usd"),
        "per_symbol": block.get("per_symbol_realized") or [],
    }


def _session_policy_slice(op: dict[str, Any], sleeve: str) -> dict[str, Any]:
    sp = (op.get(sleeve) or {}).get("session_policy") or {}
    return {
        "mode": sp.get("mode"),
        "session_expectancy_usd": sp.get("session_expectancy_usd"),
        "session_exits": sp.get("session_exits"),
        "notes": (sp.get("notes") or [])[-4:],
    }


def _top_blocks(op: dict[str, Any], sleeve: str) -> list[Any]:
    return list(((op.get(sleeve) or {}).get("blocks") or {}).get("top_blocks") or [])[:5]


def build_si_effectiveness_slice(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """SI effectiveness + open queue + recommended fixes."""
    if metrics is None:
        from utils.si_capability_review import collect_metrics

        metrics = collect_metrics()

    from utils.si_capability_review import evaluate_objective_gaps
    from utils.si_intervention_log import intervention_success_rate
    from utils.si_recommendation_queue import load_queue

    rate = intervention_success_rate(metrics)
    gaps = evaluate_objective_gaps(metrics)
    skim = metrics.get("skim_swarm") or {}
    infra = metrics.get("infra_swarm") or {}

    open_items = [
        i
        for i in (load_queue().get("items") or [])
        if isinstance(i, dict) and i.get("status") == "open"
    ]
    by_disp: dict[str, int] = {}
    for i in open_items:
        d = str(i.get("disposition") or "unknown")
        by_disp[d] = by_disp.get(d, 0) + 1

    deferred = [
        {
            "id": i.get("id"),
            "code": i.get("code"),
            "execute_after_et": i.get("execute_after_et"),
            "disposition": i.get("disposition"),
        }
        for i in open_items
        if i.get("execute_after_et")
    ]

    recommendations = recommend_si_fixes(
        rate=rate,
        gaps=gaps,
        skim=skim,
        infra=infra,
        open_by_disposition=by_disp,
    )

    predictability: dict[str, Any] = {}
    try:
        from utils.si_predictability import score_prediction_accuracy

        predictability = score_prediction_accuracy(metrics)
    except Exception:
        predictability = {"accuracy": None, "scored": 0}

    if predictability.get("accuracy") is not None and float(predictability["accuracy"]) < 0.55:
        recommendations.append(
            f"SI prediction accuracy {predictability['accuracy']:.2f}<0.55 — "
            "review brake/tight forecasts vs realized expectancy (si_predictability)."
        )
        recommendations = recommendations[:8]

    gap_ids = {str(g.get("objective_id") or "") for g in gaps}
    if "portfolio_session_alpha_vs_spy" in gap_ids or "portfolio_participation_on_strong_tape" in gap_ids:
        recommendations.insert(
            0,
            "Strong-tape alpha/participation gap — dispatch constructive_tape_override + "
            "swarm_session_adapt; do not disable market_relative gate.",
        )
        recommendations = recommendations[:8]

    return {
        "intervention_success_rate": round(float(rate), 4) if rate is not None else None,
        "target_min": 0.35,
        "prediction_accuracy": predictability.get("accuracy"),
        "prediction_scored": predictability.get("scored"),
        "skim_rolling_expectancy_usd": skim.get("rolling_expectancy_usd"),
        "skim_rolling_payoff_ratio": skim.get("rolling_payoff_ratio"),
        "infra_rolling_expectancy_usd": infra.get("rolling_expectancy_usd"),
        "objective_gaps": [
            {
                "priority": g.get("priority"),
                "objective_id": g.get("objective_id"),
                "metric": g.get("metric"),
                "value": g.get("value"),
                "target": g.get("target_min", g.get("target_max")),
            }
            for g in gaps
        ],
        "open_queue_by_disposition": by_disp,
        "deferred_auto_implement": deferred,
        "recommended_fixes": recommendations,
        "marker": "si_effectiveness_in_performance_report",
    }


def recommend_si_fixes(
    *,
    rate: float | None,
    gaps: list[dict[str, Any]],
    skim: dict[str, Any],
    infra: dict[str, Any],
    open_by_disposition: dict[str, int],
) -> list[str]:
    """Bounded, actionable next steps — never 'loosen pre_trade_gate'."""
    out: list[str] = []
    gap_ids = {str(g.get("objective_id") or "") for g in gaps}

    try:
        from utils.edge_autofix import overlays_at_cap

        if overlays_at_cap("skim_swarm"):
            out.append(
                "Skim overlays at SI caps — rely on symbol_session_brake / pattern disable, "
                "not more RR/target boosts (edge_autofix_exhausted)."
            )
    except Exception:
        pass

    pay = skim.get("rolling_payoff_ratio")
    if pay is not None and float(pay) < 1.0:
        out.append(
            f"Skim payoff {float(pay):.2f}<1.0 — enforce symbol brakes on large losers; "
            "trim mega-cap clip size after adverse excursion."
        )
    exp = skim.get("rolling_expectancy_usd")
    if exp is not None and float(exp) < 0:
        out.append(
            f"Skim rolling expectancy {float(exp):.4f}<0 — keep session mode tight; "
            "prefer pause_entries on repeat losers over more entries."
        )
    iexp = infra.get("rolling_expectancy_usd")
    if iexp is not None and float(iexp) < 0:
        out.append(
            f"Infra rolling expectancy {float(iexp):.4f}<0 — demote idle infra or "
            "fix L1/L2 score clearance before boosting max-open."
        )

    if rate is not None and float(rate) < 0.35:
        out.append(
            f"SI intervention_success_rate={float(rate):.2f}<0.35 — only count post-format "
            "scoreable actions; escalate gap→action registry dispatch."
        )
    elif rate is None:
        out.append(
            "SI intervention_success_rate=None (no scoreable post-fix interventions yet) — "
            "wait for symbol_session_brake / material edge action, then re-score."
        )

    if "portfolio_session_alpha_vs_spy" in gap_ids or "portfolio_participation_on_strong_tape" in gap_ids:
        out.append(
            "Strong-tape alpha/participation gap — SI runs deep_lag_wait / denylist_audit / "
            "infra_strong_tape_soft (never disables market_relative gate)."
        )

    pending_auto = int(open_by_disposition.get("auto_implement_queued") or 0)
    if pending_auto:
        out.append(
            f"{pending_auto} auto_implement_queued — reconcile deployed guards so velocity "
            "is free (deployed_guard_auto_implement_noise)."
        )

    pending_human = int(open_by_disposition.get("pending_human_go") or 0)
    if pending_human:
        out.append(
            f"{pending_human} pending_human_go items — classic cross-stack stays monitor-only."
        )

    if not out:
        out.append("No urgent SI fixes — monitor rolling expectancy/payoff next session.")
    return out[:8]


def build_performance_report(*, include_metrics: bool = True) -> dict[str, Any]:
    """Full performance + SI effectiveness snapshot (America/New_York session)."""
    op_path = _data_dir() / "operator_status" / "latest.json"
    op: dict[str, Any] = {}
    if op_path.is_file():
        try:
            doc = json.loads(op_path.read_text(encoding="utf-8"))
            op = doc if isinstance(doc, dict) else {}
        except Exception:
            op = {}

    metrics: dict[str, Any] | None = None
    if include_metrics:
        try:
            from utils.si_capability_review import collect_metrics

            metrics = collect_metrics()
        except Exception:
            metrics = None

    skim = _sleeve_pnl(op, "skim")
    infra = _sleeve_pnl(op, "infra")
    try:
        skim_r = float(skim.get("realized_usd") or 0)
        infra_r = float(infra.get("realized_usd") or 0)
        combined = round(skim_r + infra_r, 4)
    except (TypeError, ValueError):
        combined = None

    port: dict[str, Any] = {}
    try:
        from utils.market_benchmark import build_portfolio_session_metrics

        port = build_portfolio_session_metrics()
    except Exception:
        port = {}

    si = build_si_effectiveness_slice(metrics)

    return {
        "ok": True,
        "ts": now_iso(),
        "session_date_et": now().date().isoformat(),
        "operator_ts": op.get("ts"),
        "pnl": {
            "skim": skim,
            "infra": infra,
            "combined_realized_usd": combined,
        },
        "session_policy": {
            "skim": _session_policy_slice(op, "skim"),
            "infra": _session_policy_slice(op, "infra"),
        },
        "top_blocks": {
            "skim": _top_blocks(op, "skim"),
            "infra": _top_blocks(op, "infra"),
        },
        "portfolio": {
            "alpha_vs_spy_pct": port.get("alpha_vs_spy_pct"),
            "session_exit_count": port.get("session_exit_count"),
            "session_realized_usd": port.get("session_realized_usd"),
            "strong_tape_1d": port.get("strong_tape_1d"),
            "participation_shortfall_exits": port.get("participation_shortfall_exits"),
        },
        "si_effectiveness": si,
        "anomalies": op.get("anomalies") or [],
        "marker": "performance_report_with_si",
    }


def format_performance_report_markdown(report: dict[str, Any] | None = None) -> str:
    """Human-readable performance + SI block for operators / chat."""
    report = report or build_performance_report()
    pnl = report.get("pnl") or {}
    skim = pnl.get("skim") or {}
    infra = pnl.get("infra") or {}
    si = report.get("si_effectiveness") or {}
    port = report.get("portfolio") or {}

    lines = [
        f"## Performance ({report.get('session_date_et')} ET)",
        "",
        f"| Sleeve | Realized | Exits |",
        f"|--------|----------|-------|",
        f"| Skim | ${skim.get('realized_usd')} | {skim.get('exit_count')} |",
        f"| Infra | ${infra.get('realized_usd')} | {infra.get('exit_count')} |",
        f"| Combined | ${pnl.get('combined_realized_usd')} | — |",
        "",
        f"Alpha vs SPY: {port.get('alpha_vs_spy_pct')} | exits={port.get('session_exit_count')} | "
        f"strong_tape={port.get('strong_tape_1d')} | shortfall={port.get('participation_shortfall_exits')}",
        "",
        "## SI effectiveness",
        "",
        f"- intervention_success_rate: **{si.get('intervention_success_rate')}** "
        f"(target ≥ {si.get('target_min')})",
        f"- prediction_accuracy: {si.get('prediction_accuracy')} "
        f"(scored={si.get('prediction_scored')})",
        f"- skim rolling expectancy: {si.get('skim_rolling_expectancy_usd')} | "
        f"payoff: {si.get('skim_rolling_payoff_ratio')}",
        f"- infra rolling expectancy: {si.get('infra_rolling_expectancy_usd')}",
        f"- open queue: {si.get('open_queue_by_disposition')}",
        "",
        "### Recommended fixes",
    ]
    for fix in si.get("recommended_fixes") or []:
        lines.append(f"- {fix}")
    if si.get("deferred_auto_implement"):
        lines.append("")
        lines.append("### Deferred auto-implement")
        for d in si["deferred_auto_implement"]:
            lines.append(
                f"- `{d.get('code')}` after {d.get('execute_after_et')} ({d.get('disposition')})"
            )
    return "\n".join(lines)


__all__ = [
    "build_performance_report",
    "build_si_effectiveness_slice",
    "format_performance_report_markdown",
    "recommend_si_fixes",
]
