"""
Structured cron job heartbeats — success timestamps + failure logging.

Used by scripts/cron_run.sh after orchestrator invocations.
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_STORE = _ROOT / "data" / "cron_heartbeats.json"
_FAILURE_LOG = _ROOT / "logs" / "cron_job_failures.log"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_store(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_STORE
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def write_store(doc: dict[str, Any], path: Path | None = None) -> None:
    p = path or _DEFAULT_STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(p)


def record_success(job: str, *, path: Path | None = None) -> dict[str, Any]:
    """Merge heartbeat entry for successful completion."""
    doc = read_store(path)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        jobs = {}
    prev = jobs.get(job)
    if not isinstance(prev, dict):
        prev = {}
    prev.update(
        {
            "last_success_at": _utc_now(),
            "last_exit_code": 0,
            "last_failure_at": prev.get("last_failure_at"),
            "last_failure_detail": prev.get("last_failure_detail"),
        }
    )
    jobs[job] = prev
    doc["jobs"] = jobs
    doc["updated_at"] = _utc_now()
    write_store(doc, path)
    return prev


def record_failure(job: str, exit_code: int, detail: str | None = None, *, path: Path | None = None) -> None:
    doc = read_store(path)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        jobs = {}
    prev = jobs.get(job)
    if not isinstance(prev, dict):
        prev = {}
    prev.update(
        {
            "last_failure_at": _utc_now(),
            "last_exit_code": int(exit_code),
            "last_failure_detail": (detail or "")[:2000],
        }
    )
    jobs[job] = prev
    doc["jobs"] = jobs
    doc["updated_at"] = _utc_now()
    write_store(doc, path)
    _FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{_utc_now()} job={job} rc={exit_code} detail={detail or ''}\n"
    try:
        with open(_FAILURE_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def evaluate_heartbeat_health(
    manifest: list[dict[str, Any]],
    *,
    store_path: Path | None = None,
) -> dict[str, Any]:
    """
    Compare last_success_at vs expected_interval_minutes * 2 (alert if stale).
    """
    store = read_store(store_path).get("jobs") or {}
    if not isinstance(store, dict):
        store = {}
    now = datetime.now(timezone.utc)
    alerts: list[dict[str, Any]] = []
    ok_jobs = 0
    for row in manifest:
        name = str(row.get("job_name") or "").strip()
        if not name:
            continue
        interval = float(row.get("expected_interval_minutes") or 60)
        stale_mult = float(os.getenv("FORTRESS_CRON_HEARTBEAT_STALE_MULT", "2"))
        max_age_min = interval * stale_mult
        ent = store.get(name)
        last_ok = None
        if isinstance(ent, dict):
            raw = ent.get("last_success_at")
            if raw:
                try:
                    last_ok = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except Exception:
                    last_ok = None
        if last_ok is None:
            soft = str(os.getenv("FORTRESS_CRON_HEARTBEAT_SOFT_LAUNCH", "1")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            alerts.append(
                {
                    "job": name,
                    "severity": "warn" if soft else "fail",
                    "reason": "never_reported_success",
                }
            )
            continue
        age_min = (now - last_ok).total_seconds() / 60.0
        if age_min > max_age_min:
            alerts.append(
                {
                    "job": name,
                    "severity": "fail",
                    "reason": f"stale_success age_min={age_min:.1f} max={max_age_min:.1f}",
                }
            )
        else:
            ok_jobs += 1
    worst = "ok"
    for a in alerts:
        sev = a.get("severity") or "fail"
        if sev == "fail":
            worst = "fail"
            break
        if sev == "warn" and worst == "ok":
            worst = "warn"
    overall = worst if alerts else "ok"
    return {
        "overall": overall,
        "ok_jobs": ok_jobs,
        "alerts": alerts,
        "manifest_jobs": len(manifest),
    }


def load_manifest() -> list[dict[str, Any]]:
    mp = _ROOT / "deploy" / "cron_manifest.json"
    if not mp.exists():
        return []
    try:
        raw = json.loads(mp.read_text(encoding="utf-8"))
        jobs = raw.get("jobs")
        return jobs if isinstance(jobs, list) else []
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Fortress cron heartbeat recorder")
    ap.add_argument("--job", required=True)
    ap.add_argument("--ok", action="store_true")
    ap.add_argument("--failure", type=int, default=None)
    ap.add_argument("--detail", default="")
    args = ap.parse_args()
    try:
        if args.ok:
            record_success(args.job)
            return 0
        if args.failure is not None:
            record_failure(args.job, args.failure, args.detail)
            return 0
        print("Specify --ok or --failure", flush=True)
        return 2
    except Exception:
        print(traceback.format_exc(), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
