"""Continuous SI orchestrator — outcomes → findings → auto-assess → auto-code.

Always-on improvement path (timer + governance + optional RTH). Does not require
the user to ask for a performance review.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from utils.system_time import now_iso

log = logging.getLogger(__name__)
_MARKER = "si_continuous_cycle"


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def close_stale_not_worth_open(*, limit: int = 40) -> list[str]:
    """Close open items already assessed not-worth-implementing (noise restart)."""
    from utils.si_recommendation_queue import (
        STATUS_CLOSED,
        STATUS_OPEN,
        is_cross_stack_item,
        load_queue,
        save_queue,
    )

    queue = load_queue()
    closed: list[str] = []
    changed = False
    for item in queue.get("items") or []:
        if len(closed) >= limit:
            break
        if not isinstance(item, dict) or item.get("status") != STATUS_OPEN:
            continue
        ass = item.get("agent_assessment") if isinstance(item.get("agent_assessment"), dict) else {}
        disp = str(item.get("disposition") or "")
        kind = str(item.get("kind") or "")
        # Classic monitors / not-worth never consume auto-code capacity.
        dismiss = ass.get("worth_implementing") is False or (
            is_cross_stack_item(item) and kind == "monitor" and disp in ("monitoring", "pending_agent_review", "pending_human_go")
        )
        if not dismiss:
            continue
        item["status"] = STATUS_CLOSED
        item["disposition"] = "dismissed"
        item["closed_reason"] = (
            "stale_not_worth_reopen" if ass.get("worth_implementing") is False else "cross_stack_monitor_noise"
        )
        item["updated_utc"] = now_iso()
        closed.append(str(item.get("code") or item.get("id") or ""))
        changed = True
    if changed:
        save_queue(queue)
    return closed


def clear_stuck_implementing_status(*, max_age_hours: float = 4.0) -> list[str]:
    """Unstick items left in code_implementation.status=implementing after crashes."""
    from datetime import timedelta

    from utils.si_recommendation_queue import STATUS_OPEN, load_queue, save_queue
    from utils.system_time import now, parse_iso

    queue = load_queue()
    cleared: list[str] = []
    changed = False
    cutoff = now() - timedelta(hours=max(0.5, max_age_hours))
    for item in queue.get("items") or []:
        if not isinstance(item, dict) or item.get("status") != STATUS_OPEN:
            continue
        impl = item.get("code_implementation") if isinstance(item.get("code_implementation"), dict) else {}
        if str(impl.get("status") or "") != "implementing":
            continue
        started = str(impl.get("started_utc") or "")
        try:
            st = parse_iso(started) if started else None
        except Exception:
            st = None
        if st is None or st <= cutoff:
            impl["status"] = "stale_cleared"
            impl["cleared_utc"] = now_iso()
            item["code_implementation"] = impl
            item["updated_utc"] = now_iso()
            cleared.append(str(item.get("id") or ""))
            changed = True
    if changed:
        save_queue(queue)
    return cleared


def reset_retriable_blocked_auto_implement(*, limit: int = 5) -> list[str]:
    """Allow auto_implement_queued items previously blocked by data/* diffs to retry."""
    from utils.si_recommendation_queue import (
        DISPOSITION_AUTO_IMPLEMENT_QUEUED,
        STATUS_OPEN,
        load_queue,
        save_queue,
    )

    queue = load_queue()
    reset: list[str] = []
    changed = False
    for item in queue.get("items") or []:
        if len(reset) >= limit:
            break
        if not isinstance(item, dict) or item.get("status") != STATUS_OPEN:
            continue
        if str(item.get("disposition") or "") != DISPOSITION_AUTO_IMPLEMENT_QUEUED:
            continue
        impl = item.get("code_implementation") if isinstance(item.get("code_implementation"), dict) else {}
        err = str(impl.get("error") or "")
        status = str(impl.get("status") or "")
        retriable = status in ("blocked", "failed", "frozen") and (
            "data/" in err or "outside_allowlist:data" in err or "no_allowed_code_changes" in err
            or status in ("blocked", "failed")
        )
        if not retriable:
            continue
        # Keep last result but clear so can_auto_implement proceeds.
        impl["status"] = "retry_pending"
        impl["retry_utc"] = now_iso()
        item["code_implementation"] = impl
        item["updated_utc"] = now_iso()
        reset.append(str(item.get("code") or item.get("id") or ""))
        changed = True
    if changed:
        save_queue(queue)
    return reset


def run_continuous_si_cycle(*, skip_code: bool = False) -> dict[str, Any]:
    """
    Full continuous improvement cycle:
    1. Close stale not-worth queue noise
    2. Integrity scan + opportunities → recommendation queue
    3. Capability / performance snapshot (for operators + SI model)
    4. Autonomous code assess + implement (unless skip_code)
    5. Persist report under data/si_continuous/
    """
    from utils.operator_halt import is_trading_halted

    out: dict[str, Any] = {
        "ok": True,
        "ts": now_iso(),
        "marker": _MARKER,
    }
    if is_trading_halted():
        out["ok"] = True
        out["skipped"] = "SI-FROZEN: trading_halted"
        out["frozen"] = True
        _persist(out)
        return out

    out["stale_closed"] = close_stale_not_worth_open()
    out["stuck_cleared"] = clear_stuck_implementing_status()
    out["retry_reset"] = reset_retriable_blocked_auto_implement()

    try:
        from utils.integrity_diagnostics import run_integrity_scan
        from utils.si_recommendation_queue import process_scan_to_queue, status_dict

        scan = run_integrity_scan(log=False)
        out["integrity"] = {
            "counts": scan.get("counts"),
            "findings": len(scan.get("findings") or []),
        }
        # process_scan_to_queue already runs autonomous_code_si unless we temporarily disable.
        prev_auto = os.environ.get("FORTRESS_SI_AUTO_CODE")
        if skip_code:
            os.environ["FORTRESS_SI_AUTO_CODE"] = "0"
        try:
            summary = process_scan_to_queue(scan)
        finally:
            if skip_code:
                if prev_auto is None:
                    os.environ.pop("FORTRESS_SI_AUTO_CODE", None)
                else:
                    os.environ["FORTRESS_SI_AUTO_CODE"] = prev_auto
        if skip_code and isinstance(summary, dict):
            summary = dict(summary)
            summary["autonomous_code_si"] = {"skipped": "skip_code_flag"}
        out["queue"] = {
            "findings_processed": summary.get("findings_processed"),
            "auto_applied": summary.get("auto_applied"),
            "auto_resolved": summary.get("auto_resolved"),
            "autonomous_code_si": summary.get("autonomous_code_si"),
        }
        out["queue_status"] = status_dict()
        out["autonomous_code_si"] = summary.get("autonomous_code_si")
    except Exception as e:
        log.exception("continuous_si_queue_failed")
        out["ok"] = False
        out["queue_error"] = str(e)[:200]

    try:
        from utils.si_capability_review import run_capability_review_cycle

        out["capability_review"] = run_capability_review_cycle(apply=True)
    except Exception as e:
        out["capability_review"] = {"error": str(e)[:120]}

    try:
        from utils.si_participation_actions import run_participation_si_cycle

        metrics = (out.get("capability_review") or {}).get("metrics") or {}
        out["participation_si"] = run_participation_si_cycle(metrics=metrics)
    except Exception as e:
        out["participation_si"] = {"error": str(e)[:120]}

    try:
        from utils.si_adaptive_actions import run_adaptive_si_cycle

        gaps = (out.get("capability_review") or {}).get("objective_gaps") or []
        out["adaptive_si"] = run_adaptive_si_cycle(gaps=gaps)
    except Exception as e:
        out["adaptive_si"] = {"error": str(e)[:120]}

    try:
        from utils.performance_report import build_performance_report

        out["performance"] = build_performance_report(include_metrics=True)
    except Exception as e:
        out["performance"] = {"error": str(e)[:120]}

    if not skip_code and not (out.get("autonomous_code_si") or {}).get("implementations"):
        # Extra implement pass when queue process skipped due to race / zero limit remainder
        try:
            from utils.si_code_implementation import auto_code_enabled, run_autonomous_code_si_cycle

            if auto_code_enabled():
                out["autonomous_code_si_extra"] = run_autonomous_code_si_cycle(
                    assess_limit=int(os.environ.get("FORTRESS_SI_AUTO_ASSESS_LIMIT", "8") or 8),
                    implement_limit=int(os.environ.get("FORTRESS_SI_AUTO_IMPLEMENT_LIMIT", "1") or 1),
                )
        except Exception as e:
            out["autonomous_code_si_extra"] = {"error": str(e)[:120]}

    _persist(out)
    return out


def _persist(doc: dict[str, Any]) -> None:
    d = _data_dir() / "si_continuous"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    log_path = d / "cycle_log.jsonl"
    brief = {
        "ts": doc.get("ts"),
        "ok": doc.get("ok"),
        "marker": _MARKER,
        "findings": (doc.get("integrity") or {}).get("findings"),
        "stale_closed": len(doc.get("stale_closed") or []),
        "auto_code": (doc.get("autonomous_code_si") or {}).get("implemented"),
        "assessed": (doc.get("autonomous_code_si") or {}).get("assessed"),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(brief, default=str) + "\n")


__all__ = [
    "close_stale_not_worth_open",
    "clear_stuck_implementing_status",
    "reset_retriable_blocked_auto_implement",
    "run_continuous_si_cycle",
]
