"""
Recursive SI recommendation queue — learn, auto-correct, escalate for agent/human review.

Flow:
1. integrity + opportunity scans produce findings
2. fix registry maps findings → auto-tunable nudges or code-guard mitigation checks
3. tunable corrections apply within governance bounds (Tier 0/1)
4. unresolved code/ops items → pending_agent_review → auto-assess → auto_implement (no human go)
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from utils.system_time import ensure_system_tz, now_iso, system_tz_name

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent

DISPOSITION_AUTO_RESOLVED = "auto_resolved"
DISPOSITION_AUTO_APPLIED = "auto_applied"
DISPOSITION_MONITORING = "monitoring"
DISPOSITION_PENDING_AGENT = "pending_agent_review"
DISPOSITION_PENDING_HUMAN = "pending_human_go"
DISPOSITION_AUTO_IMPLEMENT_QUEUED = "auto_implement_queued"
DISPOSITION_DISMISSED = "dismissed"

CROSS_STACK_SOURCES = frozenset({"cross_stack_belief", "fortress_ai_belief", "capability_review"})


def is_cross_stack_source(source: str) -> bool:
    return str(source or "") in CROSS_STACK_SOURCES


def is_cross_stack_item(item: dict[str, Any] | None) -> bool:
    """True when item originated from or is tagged as cross-stack belief sharing."""
    if not isinstance(item, dict):
        return False
    if item.get("cross_stack"):
        return True
    return is_cross_stack_source(str(item.get("source") or ""))


CROSS_STACK_FORBIDDEN_AUTO_DISPOSITIONS = frozenset(
    {
        DISPOSITION_AUTO_RESOLVED,
        DISPOSITION_AUTO_APPLIED,
        DISPOSITION_AUTO_IMPLEMENT_QUEUED,
        "auto_apply_queued",
    }
)

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_IMPLEMENTED = "implemented"


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def queue_path() -> Path:
    return _data_dir() / "si_recommendation_queue.json"


def queue_log_path() -> Path:
    return _data_dir() / "si_recommendation_queue.jsonl"


def fix_registry_path() -> Path:
    return _ROOT / "config" / "si_fix_registry.json"


def load_fix_registry() -> dict[str, Any]:
    p = fix_registry_path()
    if not p.exists():
        return {"fixes": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"fixes": {}}
    except Exception:
        return {"fixes": {}}


def load_queue() -> dict[str, Any]:
    p = queue_path()
    if not p.exists():
        return {"version": 1, "items": []}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            return doc
    except Exception:
        pass
    return {"version": 1, "items": []}


def save_queue(doc: dict[str, Any]) -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    queue_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _append_log(record: dict[str, Any]) -> None:
    p = queue_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _now_iso() -> str:
    return now_iso()


def _finding_key(code: str, component: str = "", *, objective_id: str = "") -> str:
    base = f"{component}:{code}" if component else str(code)
    if objective_id and code == "si_objective_gap":
        return f"{base}:{objective_id}"
    return base


def finding_key_from_finding(finding: dict[str, Any]) -> str:
    return _finding_key(
        str(finding.get("code") or ""),
        str(finding.get("component") or ""),
        objective_id=str(finding.get("objective_id") or ""),
    )


def _finding_still_active(item: dict[str, Any], findings: list[dict[str, Any]]) -> bool:
    code = str(item.get("code") or "")
    component = str(item.get("component") or "")
    item_oid = str((item.get("finding") or {}).get("objective_id") or "")
    for f in findings:
        if str(f.get("code") or "") != code:
            continue
        if str(f.get("component") or "") != component:
            continue
        f_oid = str(f.get("objective_id") or "")
        if code == "si_objective_gap" and (item_oid or f_oid):
            if item_oid and f_oid:
                return item_oid == f_oid
            return True
        return True
    return False


def find_open_item(
    queue: dict[str, Any],
    *,
    code: str,
    component: str = "",
    finding: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    key = finding_key_from_finding(finding or {"code": code, "component": component})
    for item in queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("status") not in (STATUS_OPEN,):
            continue
        if item.get("finding_key") == key:
            return item
    return None


def register_fix_in_registry(
    *,
    code: str,
    title: str,
    kind: str = "code_guard",
    recommendation: str = "",
    effort: str = "medium",
    impact: str = "medium",
    agent_review_if_unmitigated: bool = True,
    mitigation_markers: list[str] | None = None,
    auto_tunable: dict[str, float] | None = None,
) -> None:
    """Record a deployed fix so future scans treat the pattern as known (manual / agent use)."""
    reg = load_fix_registry()
    fixes = reg.setdefault("fixes", {})
    fixes[code] = {
        "title": title,
        "kind": kind,
        "mitigation_markers": mitigation_markers or [],
        "auto_tunable": auto_tunable or {},
        "agent_review_if_unmitigated": agent_review_if_unmitigated,
        "effort": effort,
        "impact": impact,
        "recommendation": recommendation,
        "registered_at_utc": _now_iso(),
    }
    fix_registry_path().write_text(json.dumps(reg, indent=2), encoding="utf-8")
    _append_log({"event": "fix_registered", "code": code, "title": title, "ts": _now_iso()})


def mitigation_active(code: str, scan: dict[str, Any]) -> bool:
    """True when guard markers appear in recent logs and the anomaly code is absent."""
    reg = load_fix_registry().get("fixes") or {}
    meta = reg.get(code) if isinstance(reg, dict) else None
    if not isinstance(meta, dict):
        return False
    markers = meta.get("mitigation_markers") or []
    if not markers:
        return False

    active_codes = {str(f.get("code") or "") for f in scan.get("findings") or []}
    if code in active_codes:
        return False

    from utils.integrity_diagnostics import _read_jsonl_tail

    ai_rows = _read_jsonl_tail(_data_dir() / "ai_decisions.jsonl", max_lines=400)
    skim_rows = _read_jsonl_tail(_data_dir() / "skim_swarm" / "decisions.jsonl", max_lines=400)
    blob = json.dumps(ai_rows + skim_rows, default=str)
    marker_hits = sum(1 for m in markers if m in blob)
    return marker_hits >= max(1, len(markers) // 2 + 1)


def _apply_auto_tunable(code: str, tunable_delta: dict[str, float]) -> dict[str, Any] | None:
    if not tunable_delta:
        return None
    if not str(os.environ.get("FORTRESS_AI_SI_AUTO_APPLY", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "auto_apply_disabled", "code": code}

    from agents.self_improvement_engine import SelfImprovementEngine, TUNABLE_BOUNDS

    eng = SelfImprovementEngine()
    cur = eng.current_tunable_snapshot()
    applied: list[dict[str, Any]] = []
    for param, delta in tunable_delta.items():
        if param not in TUNABLE_BOUNDS:
            continue
        if param == "confidence_threshold" and float(delta) < 0:
            floor_lock = str(os.environ.get("FORTRESS_AI_CONFIDENCE_FLOOR_LOCK", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if floor_lock or code == "low_unified_execution_rate":
                continue
        try:
            base = float(cur.get(param) or 0)
            new_val = base + float(delta)
        except (TypeError, ValueError):
            continue
        raw = {
            "parameter": param,
            "current_value": base,
            "proposed_value": round(new_val, 4),
            "reasoning": f"Auto-correct from integrity finding {code}.",
            "expected_impact": "Stabilize after anomaly without weakening immutable rails.",
            "risks": "Bounded single-step nudge only.",
        }
        val = eng.validate_proposal_json(raw)
        if not val:
            continue
        from utils.improvement_governance import ImprovementGovernance, determine_governance_tier

        tier = determine_governance_tier(param)
        if tier == "tier_3_blocked":
            continue
        pid = str(uuid.uuid4())
        gov = ImprovementGovernance()
        shadow = eng.shadow_test_proposal(val)
        gd = gov.process_proposal(proposal=val, proposal_id=pid, shadow_results=shadow)
        if gd.get("decision") in ("auto_approved", "pending_veto_window"):
            applied.append({"parameter": param, "delta": delta, "governance": gd.get("decision")})
    if applied and any(a.get("parameter") in ("rsi_entry_threshold", "rsi_exit_threshold") for a in applied):
        try:
            from utils.si_rsi_auto_deploy import deploy_rsi_tunable_snapshot

            deploy_rsi_tunable_snapshot(reason=f"auto_tunable:{code}")
        except Exception:
            pass
    return {"code": code, "applied": applied} if applied else None


def upsert_from_finding(
    finding: dict[str, Any],
    *,
    source: str = "integrity_scan",
    scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    code = str(finding.get("code") or "")
    component = str(finding.get("component") or "")
    reg = load_fix_registry().get("fixes") or {}
    meta = reg.get(code, {}) if isinstance(reg, dict) else {}
    if not isinstance(meta, dict):
        meta = {}

    queue = load_queue()
    existing = find_open_item(queue, code=code, component=component, finding=finding)
    now = _now_iso()

    disposition = DISPOSITION_PENDING_AGENT
    sev = str(finding.get("severity") or "")
    if is_cross_stack_source(source):
        disposition = DISPOSITION_PENDING_AGENT
    elif sev == "info":
        disposition = DISPOSITION_AUTO_RESOLVED
    elif mitigation_active(code, scan or {"findings": [finding]}):
        disposition = DISPOSITION_AUTO_RESOLVED
    elif meta.get("kind") == "code_guard":
        try:
            from utils.si_fix_deployment import is_deployed

            if is_deployed(code) and sev not in ("critical", "high"):
                disposition = DISPOSITION_AUTO_RESOLVED
        except Exception:
            pass
    elif meta.get("kind") == "monitor":
        disposition = DISPOSITION_MONITORING
    elif meta.get("auto_tunable") and meta.get("kind") == "tunable":
        disposition = DISPOSITION_AUTO_APPLIED
    elif not meta.get("agent_review_if_unmitigated", True):
        disposition = DISPOSITION_MONITORING

    item = existing or {
        "id": str(uuid.uuid4()),
        "finding_key": finding_key_from_finding(finding),
        "created_utc": now,
        "status": STATUS_OPEN,
        "human_go": None,
        "agent_assessment": None,
    }
    item.update(
        {
            "updated_utc": now,
            "code": code,
            "component": component,
            "title": meta.get("title") or code,
            "kind": meta.get("kind") or finding.get("kind") or "unknown",
            "severity": finding.get("severity"),
            "recommendation": finding.get("recommendation") or meta.get("recommendation") or "",
            "effort": meta.get("effort") or "medium",
            "impact": meta.get("impact") or finding.get("severity") or "medium",
            "disposition": disposition,
            "source": source,
            "finding": finding,
            "cross_stack": is_cross_stack_source(source),
        }
    )

    if disposition == DISPOSITION_AUTO_RESOLVED:
        item["status"] = STATUS_CLOSED
        item["closed_reason"] = "mitigation_active"
    elif disposition == DISPOSITION_PENDING_AGENT and meta.get("agent_review_if_unmitigated", True):
        item["disposition"] = DISPOSITION_PENDING_AGENT

    if existing:
        idx = next(i for i, x in enumerate(queue["items"]) if x.get("id") == existing.get("id"))
        queue["items"][idx] = item
    else:
        queue.setdefault("items", []).append(item)

    save_queue(queue)
    _append_log({"event": "upsert", "item_id": item["id"], "code": code, "disposition": disposition})
    return item


def scan_opportunities() -> list[dict[str, Any]]:
    """Proactive improvement suggestions beyond anomaly detection."""
    out: list[dict[str, Any]] = []
    from utils.integrity_diagnostics import _read_jsonl_tail

    rows = _read_jsonl_tail(_data_dir() / "ai_decisions.jsonl", max_lines=120)
    exec_n = wait_n = 0
    for r in rows:
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        if act.get("executed"):
            exec_n += 1
        if d.get("action") == "wait":
            wait_n += 1
    n = max(len(rows), 1)
    exec_rate = exec_n / n
    if len(rows) >= 30 and exec_rate < 0.05 and wait_n > 20:
        out.append(
            {
                "code": "low_unified_execution_rate",
                "severity": "medium",
                "component": "unified_ai",
                "execution_rate": round(exec_rate, 4),
                "recommendation": (
                    "Execution rate very low — use off-denylist watchlist (FORTRESS_AI_ELIGIBLE_UNIVERSE); "
                    "do not auto-lower confidence when FORTRESS_AI_CONFIDENCE_FLOOR_LOCK=1."
                ),
                "si_action": "unified_off_denylist_watchlist",
            }
        )

    try:
        from utils.api_costs import weekly_llm_budget_status

        b = weekly_llm_budget_status()
        cap = float(b.get("cap_usd") or 0)
        spent = float(b.get("spent_usd") or 0)
        if cap > 0 and spent / cap >= 0.85:
            out.append(
                {
                    "code": "weekly_llm_budget_tight",
                    "severity": "medium",
                    "component": "unified_ai",
                    "spent_usd": spent,
                    "cap_usd": cap,
                    "recommendation": (
                        "Weekly LLM budget >=85% — raise FORTRESS_AI_WEEKLY_LLM_CAP_USD or reduce "
                        "FORTRESS_AI_SI_EVERY_N_CYCLES; agent review recommended."
                    ),
                    "si_action": "ops_budget_review",
                }
            )
    except Exception:
        pass

    skim_learned = _data_dir() / "skim_swarm" / "learned"
    if skim_learned.is_dir():
        try:
            from utils.skim_swarm_config import target_winning_pattern_share
            from utils.skim_pattern_review import swarm_winning_pattern_share

            goal = target_winning_pattern_share()
            portfolio_share = swarm_winning_pattern_share(min_exits=3)
            if portfolio_share is not None and portfolio_share < goal:
                out.append(
                    {
                        "code": "skim_winning_pattern_share_low",
                        "severity": "high",
                        "component": "skim_swarm",
                        "avg_share": round(portfolio_share, 4),
                        "target": goal,
                        "recommendation": (
                            f"Portfolio winning-pattern share {portfolio_share:.2%} below target "
                            f"{goal:.0%} — running skim_pattern_review (lifetime stats)."
                        ),
                        "si_action": "skim_pattern_review",
                    }
                )
        except Exception:
            pass

    return out


def reconcile_deployed_guards(scan: dict[str, Any]) -> list[str]:
    """Close open queue items for deployed code guards with no critical/high findings."""
    active = {
        str(f.get("code") or "")
        for f in scan.get("findings") or []
        if str(f.get("severity") or "") in ("critical", "high")
    }
    closed: list[str] = []
    queue = load_queue()
    changed = False
    for i, item in enumerate(queue.get("items") or []):
        if not isinstance(item, dict) or item.get("status") != STATUS_OPEN:
            continue
        if is_cross_stack_item(item):
            continue
        code = str(item.get("code") or "")
        if code in active:
            continue
        try:
            from utils.si_fix_deployment import is_deployed

            if not is_deployed(code):
                continue
        except Exception:
            continue
        item["status"] = STATUS_IMPLEMENTED
        item["disposition"] = DISPOSITION_AUTO_RESOLVED
        item["closed_reason"] = "deployed_guard_no_active_findings"
        item["updated_utc"] = _now_iso()
        queue["items"][i] = item
        closed.append(code)
        changed = True
    if changed:
        save_queue(queue)
    return closed


def reconcile_cleared_findings(
    scan: dict[str, Any],
    *,
    active_findings: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Auto-close open SI items when scans no longer report the underlying finding."""
    findings = active_findings if active_findings is not None else list(scan.get("findings") or [])
    queue = load_queue()
    closed: list[str] = []
    changed = False
    for i, item in enumerate(queue.get("items") or []):
        if not isinstance(item, dict) or item.get("status") != STATUS_OPEN:
            continue
        if item.get("disposition") == DISPOSITION_PENDING_HUMAN:
            continue
        if item.get("implementation_ready"):
            continue
        if item.get("disposition") == DISPOSITION_AUTO_IMPLEMENT_QUEUED:
            continue
        if is_cross_stack_item(item):
            continue
        if _finding_still_active(item, findings):
            continue
        code = str(item.get("code") or "")
        item["status"] = STATUS_IMPLEMENTED
        item["disposition"] = DISPOSITION_AUTO_RESOLVED
        item["closed_reason"] = "finding_cleared"
        item["implemented_utc"] = _now_iso()
        item["implementation_note"] = (
            f"Auto-closed: finding no longer active ({code})."
        )[:2000]
        item["updated_utc"] = _now_iso()
        queue["items"][i] = item
        closed.append(code)
        changed = True
        _append_log({"event": "auto_resolved_stale", "item_id": item.get("id"), "code": code})
    if changed:
        save_queue(queue)
    return closed


def process_scan_to_queue(scan: dict[str, Any] | None = None) -> dict[str, Any]:
    from utils.integrity_diagnostics import run_integrity_scan

    try:
        from utils.si_fix_deployment import sync_deployed_from_registry

        sync_deployed_from_registry()
    except Exception:
        pass

    scan = scan or run_integrity_scan(log=True)
    opportunities = scan_opportunities()
    all_findings = list(scan.get("findings") or []) + opportunities

    items: list[dict[str, Any]] = []
    auto_applied: list[dict[str, Any]] = []
    pattern_review: dict[str, Any] | None = None
    for f in all_findings:
        item = upsert_from_finding(f, scan=scan)
        items.append(item)
        code = str(f.get("code") or "")
        if code == "skim_winning_pattern_share_low":
            try:
                from utils.skim_pattern_review import apply_swarm_pattern_review

                pattern_review = apply_swarm_pattern_review()
                if pattern_review.get("changes"):
                    item["auto_correct"] = pattern_review
                    item["disposition"] = DISPOSITION_AUTO_APPLIED
            except Exception:
                pass
        reg = load_fix_registry().get("fixes") or {}
        meta = reg.get(str(f.get("code") or ""), {}) if isinstance(reg, dict) else {}
        if isinstance(meta, dict) and item.get("disposition") == DISPOSITION_AUTO_APPLIED:
            tun = meta.get("auto_tunable") or {}
            if isinstance(tun, dict) and tun:
                res = _apply_auto_tunable(str(f.get("code")), {k: float(v) for k, v in tun.items()})
                if res:
                    auto_applied.append(res)
                    item["disposition"] = DISPOSITION_AUTO_APPLIED
                    item["auto_correct"] = res

    si_actions_applied: list[dict[str, Any]] = []
    try:
        from utils.si_queue.si_processor import apply_si_actions_from_findings

        si_actions_applied = apply_si_actions_from_findings(all_findings)
    except Exception:
        pass

    # Auto-close deployed guards + findings no longer reported
    reconcile_deployed_guards(scan)
    auto_resolved = reconcile_cleared_findings(scan, active_findings=all_findings)

    pending_agent = list_pending(disposition=DISPOSITION_PENDING_AGENT)
    pending_human = list_pending(disposition=DISPOSITION_PENDING_HUMAN)
    pending_auto = list_pending(disposition=DISPOSITION_AUTO_IMPLEMENT_QUEUED)

    ts = _now_iso()
    summary = {
        "timestamp": ts,
        "system_tz": system_tz_name(),
        "timestamp_utc": ts,
        "findings_processed": len(all_findings),
        "items_upserted": len(items),
        "auto_applied": auto_applied,
        "auto_resolved": auto_resolved,
        "pending_agent_review": len(pending_agent),
        "pending_human_go": len(pending_human),
        "auto_implement_queued": len(pending_auto),
        "pending_agent": pending_agent,
        "pending_human": pending_human,
        "pending_auto_implement": pending_auto,
        "si_actions_applied": si_actions_applied,
    }
    if pattern_review:
        summary["skim_pattern_review"] = pattern_review

    try:
        from utils.si_code_implementation import run_autonomous_code_si_cycle

        summary["autonomous_code_si"] = run_autonomous_code_si_cycle(
            assess_limit=int(os.environ.get("FORTRESS_SI_AUTO_ASSESS_LIMIT", "5") or 5),
            implement_limit=int(os.environ.get("FORTRESS_SI_AUTO_IMPLEMENT_LIMIT", "1") or 1),
        )
    except Exception as e:
        summary["autonomous_code_si"] = {"error": str(e)[:120]}

    snap = _data_dir() / "si_recommendation_summary.json"
    snap.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def list_pending(*, disposition: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    queue = load_queue()
    out: list[dict[str, Any]] = []
    for item in queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("status") != STATUS_OPEN:
            continue
        if disposition and item.get("disposition") != disposition:
            continue
        out.append(item)
    out.sort(key=lambda x: str(x.get("updated_utc") or ""), reverse=True)
    return out[:limit]


def set_agent_assessment(
    item_id: str,
    *,
    worth_implementing: bool,
    rationale: str,
    proposed_implementation: str = "",
    reviewer: str = "cursor_agent",
) -> dict[str, Any]:
    queue = load_queue()
    for i, item in enumerate(queue.get("items") or []):
        if item.get("id") != item_id:
            continue
        item["agent_assessment"] = {
            "worth_implementing": bool(worth_implementing),
            "rationale": rationale[:4000],
            "proposed_implementation": proposed_implementation[:8000],
            "reviewer": reviewer,
            "assessed_utc": _now_iso(),
        }
        if worth_implementing:
            if is_cross_stack_item(item):
                item["disposition"] = DISPOSITION_PENDING_HUMAN
                item["requires_human_go"] = True
            else:
                try:
                    from utils.si_code_implementation import auto_code_enabled

                    if auto_code_enabled():
                        item["disposition"] = DISPOSITION_AUTO_IMPLEMENT_QUEUED
                    else:
                        item["disposition"] = DISPOSITION_PENDING_HUMAN
                except Exception:
                    item["disposition"] = DISPOSITION_PENDING_HUMAN
        else:
            item["disposition"] = DISPOSITION_DISMISSED
            item["status"] = STATUS_CLOSED
            item["closed_reason"] = "agent_dismissed"
        item["updated_utc"] = _now_iso()
        queue["items"][i] = item
        save_queue(queue)
        _append_log({"event": "agent_assessment", "item_id": item_id, "worth": worth_implementing})
        return item
    raise KeyError(f"item_not_found:{item_id}")


def set_human_go(item_id: str, *, approved: bool, note: str = "") -> dict[str, Any]:
    queue = load_queue()
    for i, item in enumerate(queue.get("items") or []):
        if item.get("id") != item_id:
            continue
        item["human_go"] = {
            "approved": bool(approved),
            "note": note[:2000],
            "decided_utc": _now_iso(),
        }
        if approved:
            item["disposition"] = DISPOSITION_PENDING_HUMAN
            item["status"] = STATUS_OPEN
            item["implementation_ready"] = True
        else:
            item["status"] = STATUS_CLOSED
            item["disposition"] = DISPOSITION_DISMISSED
            item["closed_reason"] = "human_rejected"
        item["updated_utc"] = _now_iso()
        queue["items"][i] = item
        save_queue(queue)
        _append_log({"event": "human_go", "item_id": item_id, "approved": approved})
        return item
    raise KeyError(f"item_not_found:{item_id}")


def mark_implemented(item_id: str, *, note: str = "") -> dict[str, Any]:
    queue = load_queue()
    for i, item in enumerate(queue.get("items") or []):
        if item.get("id") != item_id:
            continue
        item["status"] = STATUS_IMPLEMENTED
        item["disposition"] = DISPOSITION_AUTO_RESOLVED
        item["implemented_utc"] = _now_iso()
        item["implementation_note"] = note[:2000]
        item["updated_utc"] = _now_iso()
        queue["items"][i] = item
        save_queue(queue)
        _append_log({"event": "implemented", "item_id": item_id})
        return item
    raise KeyError(f"item_not_found:{item_id}")


def mark_implemented_by_code(code: str, *, note: str = "") -> list[dict[str, Any]]:
    """Close all open queue items matching anomaly code (post-ship cleanup)."""
    queue = load_queue()
    updated: list[dict[str, Any]] = []
    changed = False
    for i, item in enumerate(queue.get("items") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("code") or "") != code:
            continue
        if item.get("status") not in (STATUS_OPEN,):
            continue
        item["status"] = STATUS_IMPLEMENTED
        item["disposition"] = DISPOSITION_AUTO_RESOLVED
        item["implemented_utc"] = _now_iso()
        item["implementation_note"] = note[:2000]
        item["updated_utc"] = _now_iso()
        item["closed_reason"] = "implemented_by_code"
        queue["items"][i] = item
        updated.append(item)
        changed = True
    if changed:
        save_queue(queue)
        _append_log({"event": "implemented_by_code", "code": code, "count": len(updated)})
    return updated


def status_dict() -> dict[str, Any]:
    queue = load_queue()
    pending_agent = list_pending(disposition=DISPOSITION_PENDING_AGENT)
    pending_human = list_pending(disposition=DISPOSITION_PENDING_HUMAN)
    pending_auto = list_pending(disposition=DISPOSITION_AUTO_IMPLEMENT_QUEUED)
    return {
        "queue_size": len(queue.get("items") or []),
        "pending_agent_review": pending_agent,
        "pending_human_go": pending_human,
        "auto_implement_queued": pending_auto,
        "autonomous_code_enabled": _autonomous_code_flag(),
        "fix_registry_path": str(fix_registry_path()),
        "summary_path": str(_data_dir() / "si_recommendation_summary.json"),
    }


def _autonomous_code_flag() -> bool:
    try:
        from utils.si_code_implementation import auto_code_enabled

        return auto_code_enabled()
    except Exception:
        return False
