"""RTH intraday autonomous SI — anomaly scan + fixes every 30 minutes during market hours."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def rth_intraday_si_enabled() -> bool:
    return str(os.environ.get("FORTRESS_RTH_INTRADAY_SI", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def rth_cycle_interval_sec() -> int:
    try:
        return max(300, int(os.environ.get("FORTRESS_RTH_SI_INTERVAL_SEC", "1800") or 1800))
    except ValueError:
        return 1800


def _session_date_et() -> str:
    from agents.skim_swarm.eod import session_date_et

    return session_date_et()


def _swarm_data_dir(component: str) -> Path:
    if component == "infra_swarm":
        from utils.infra_swarm_config import swarm_data_dir

        return swarm_data_dir()
    from utils.skim_swarm_config import swarm_data_dir

    return swarm_data_dir()


def swarm_window_tune(component: str, *, minutes: int = 30) -> dict[str, Any]:
    """Analyze recent window and apply swarm-level runtime overrides."""
    from scripts.skim_swarm_analyze import analyze, auto_tune

    report = analyze(minutes=minutes, component=component)
    tune = auto_tune(report, component=component) if report.get("ok") else None
    return {"component": component, "report": report, "auto_tune": tune}


def run_rth_intraday_cycle(*, force: bool = False) -> dict[str, Any]:
    """
    Full RTH autonomous cycle:
    1. Integrity scan + SI queue (auto-tunable nudges)
    2. Edge scorecard refresh + edge autofix
    3. Swarm session SI adapt
    4. Per-symbol batch improvement on losers
    5. 30-min window tune (both swarms)
    6. Critical finding immediate fixes
    7. Governance + performance monitor
    """
    from utils.us_equity_hours import is_us_equity_rth_et

    if not rth_intraday_si_enabled():
        return {"ok": False, "skipped": "rth_intraday_si_disabled"}

    if not force and not is_us_equity_rth_et():
        return {"ok": True, "skipped": "outside_rth"}

    ts = datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {"ok": True, "ts": ts, "forced": force}

    from utils.integrity_diagnostics import run_integrity_scan

    # log=False — we call process_scan_to_queue explicitly once
    scan = run_integrity_scan(log=False)
    out["integrity"] = {"counts": scan.get("counts"), "findings": len(scan.get("findings") or [])}

    from utils.si_recommendation_queue import process_scan_to_queue, status_dict

    out["queue"] = process_scan_to_queue(scan)
    out["queue_status"] = status_dict()

    session = _session_date_et()
    edge_results: dict[str, Any] = {}
    for component in ("skim_swarm", "infra_swarm"):
        from utils.edge_scorecard import compute_scorecard_from_decisions, save_scorecard
        from utils.edge_autofix import apply_edge_autofix, batch_symbol_improvement

        dec = _swarm_data_dir(component) / "decisions.jsonl"
        sc = compute_scorecard_from_decisions(dec, session_date=session)
        if sc.get("ok"):
            save_scorecard(component, sc)
        edge_fix = apply_edge_autofix(component, sc)
        sym_imp = batch_symbol_improvement(component)
        tune = swarm_window_tune(component, minutes=30)
        edge_results[component] = {
            "scorecard_exits": sc.get("exits"),
            "payoff_ratio": sc.get("payoff_ratio"),
            "edge_autofix": edge_fix,
            "symbol_improvements": sym_imp,
            "window_tune": tune.get("auto_tune"),
        }
    out["edge"] = edge_results

    session_si: dict[str, Any] = {}
    for component in ("skim_swarm", "infra_swarm"):
        from utils.swarm_session_si import adapt_swarm_session

        try:
            session_si[component] = adapt_swarm_session(component)
        except Exception as e:
            session_si[component] = {"error": str(e)[:120]}
    out["session_si"] = session_si

    from utils.edge_autofix import apply_critical_findings

    out["critical_applied"] = apply_critical_findings(list(scan.get("findings") or []))

    try:
        from agents.self_improvement_engine import get_engine
        from agents.performance_monitor import PerformanceMonitor

        gov = get_engine().process_autonomous_governance()
        if gov:
            out["governance"] = gov
        out["reversions"] = PerformanceMonitor().monitor_active_changes()
    except Exception as e:
        out["governance_error"] = str(e)[:200]

    _persist_cycle_report(out)
    return out


def _persist_cycle_report(doc: dict[str, Any]) -> None:
    d = _data_dir() / "rth_intraday_si"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    log_path = d / "cycle_log.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": doc.get("ts"),
                    "findings": doc.get("integrity", {}).get("findings"),
                    "critical_applied": len(doc.get("critical_applied") or []),
                    "edge": {
                        k: {
                            "payoff": (v or {}).get("payoff_ratio"),
                            "changes": ((v or {}).get("edge_autofix") or {}).get("changes"),
                        }
                        for k, v in (doc.get("edge") or {}).items()
                    },
                },
                default=str,
            )
            + "\n"
        )
