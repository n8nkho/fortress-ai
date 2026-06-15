"""Compact operator status snapshot — services, swarms, SI queue, auto-code health."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from utils.system_time import now_iso, system_tz_name


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def operator_status_dir() -> Path:
    return _data_dir() / "operator_status"


def _service_active(unit: str) -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (r.stdout or r.stderr or "unknown").strip()
    except Exception:
        return "unknown"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _swarm_block_summary(component: str) -> dict[str, Any]:
    from utils.skim_swarm_config import swarm_data_dir as skim_dir
    from utils.infra_swarm_config import swarm_data_dir as infra_dir

    dd = skim_dir() if component == "skim_swarm" else infra_dir()
    overrides = _read_json(dd / "runtime_overrides.json")
    report = overrides.get("last_report") or {}
    top_blocks = report.get("top_blocks") or []
    return {
        "session_pnl_usd": report.get("session_realized_pnl_usd"),
        "top_blocks": top_blocks[:5],
        "waves": report.get("waves"),
        "swarm_halted": report.get("swarm_halted"),
    }


def build_operator_status() -> dict[str, Any]:
    from utils.adaptive_max_open import compute_adaptive_max_open
    from utils.swarm_session_si import effective_max_open

    services = {
        u: _service_active(u)
        for u in (
            "fortress-ai-skim-swarm",
            "fortress-ai-infra-swarm",
            "fortress-ai-dashboard",
            "fortress-ai-rth-intraday-si",
            "fortress-ai-operator-status",
        )
    }

    skim_pnl: dict[str, Any] = {}
    infra_pnl: dict[str, Any] = {}
    try:
        from agents.skim_swarm.pnl import compute_pnl_summary as skim_pnl_fn

        skim_pnl = skim_pnl_fn()
    except Exception as e:
        skim_pnl = {"error": str(e)[:120]}
    try:
        from agents.infra_swarm.pnl import compute_pnl_summary as infra_pnl_fn

        infra_pnl = infra_pnl_fn()
    except Exception as e:
        infra_pnl = {"error": str(e)[:120]}

    si_summary = _read_json(_data_dir() / "si_recommendation_summary.json")
    rth_latest = _read_json(_data_dir() / "rth_intraday_si" / "latest_cycle.json")

    cursor_cli: dict[str, Any] = {"ok": False}
    auto_code = {"enabled": False}
    try:
        from utils.si_code_implementation import auto_code_enabled, cursor_agent_resolved

        auto_code["enabled"] = auto_code_enabled()
        cursor_cli = cursor_agent_resolved()
    except Exception as e:
        cursor_cli = {"ok": False, "error": str(e)[:120]}

    anomalies: list[dict[str, Any]] = []
    try:
        from utils.integrity_diagnostics import run_integrity_scan

        scan = run_integrity_scan(log=False)
        for f in (scan.get("findings") or [])[:8]:
            if isinstance(f, dict):
                anomalies.append(
                    {
                        "code": f.get("code"),
                        "severity": f.get("severity"),
                        "component": f.get("component"),
                    }
                )
    except Exception as e:
        anomalies = [{"error": str(e)[:120]}]

    open_total = 0
    for pnl in (skim_pnl, infra_pnl):
        if isinstance(pnl, dict):
            open_total = max(open_total, int((pnl.get("daily") or {}).get("open_positions") or 0))

    return {
        "ts": now_iso(),
        "system_tz": system_tz_name(),
        "services": services,
        "portfolio": {
            "open_positions": open_total,
            "skim_max_open_effective": effective_max_open("skim_swarm"),
            "infra_max_open_effective": effective_max_open("infra_swarm"),
            "adaptive_max_open": {
                "skim": compute_adaptive_max_open("skim_swarm"),
                "infra": compute_adaptive_max_open("infra_swarm"),
            },
        },
        "skim": {
            "pnl": skim_pnl,
            "blocks": _swarm_block_summary("skim_swarm"),
            "session_policy": _read_json(_data_dir() / "skim_swarm" / "session_policy.json"),
        },
        "infra": {
            "pnl": infra_pnl,
            "blocks": _swarm_block_summary("infra_swarm"),
            "session_policy": _read_json(_data_dir() / "infra_swarm" / "session_policy.json"),
        },
        "si_queue": {
            "pending_agent_review": si_summary.get("pending_agent_review"),
            "pending_human_go": si_summary.get("pending_human_go"),
            "auto_implement_queued": si_summary.get("auto_implement_queued"),
            "autonomous_code_si": si_summary.get("autonomous_code_si") or rth_latest.get("queue", {}).get("autonomous_code_si"),
        },
        "auto_code": {
            **auto_code,
            "cursor_cli": cursor_cli,
        },
        "anomalies": anomalies,
    }


def persist_operator_status() -> dict[str, Any]:
    doc = build_operator_status()
    out_dir = operator_status_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    with (out_dir / "reports.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, default=str) + "\n")
    return doc


def format_operator_status_markdown(doc: dict[str, Any] | None = None) -> str:
    """Human-readable one-screen summary for logs / chat."""
    d = doc or build_operator_status()
    svc = d.get("services") or {}
    port = d.get("portfolio") or {}
    skim = (d.get("skim") or {}).get("pnl") or {}
    infra = (d.get("infra") or {}).get("pnl") or {}
    si = d.get("si_queue") or {}
    auto = d.get("auto_code") or {}
    lines = [
        f"**Operator status** — {d.get('ts')} ({d.get('system_tz')})",
        "",
        "**Services:** "
        + ", ".join(f"{k.split('-')[-1]}={v}" for k, v in svc.items() if "fortress" in k),
        f"**Open positions:** {port.get('open_positions')} (max skim={port.get('skim_max_open_effective')} infra={port.get('infra_max_open_effective')})",
    ]
    sd = skim.get("daily") or {}
    id_ = infra.get("daily") or {}
    if sd or id_:
        lines.append(
            f"**Session PnL:** skim ${sd.get('realized_usd', '—')} ({sd.get('exit_count', 0)} exits) | "
            f"infra ${id_.get('realized_usd', '—')} ({id_.get('exit_count', 0)} exits)"
        )
    lines.append(
        f"**SI queue:** agent={si.get('pending_agent_review')} human={si.get('pending_human_go')} auto={si.get('auto_implement_queued')}"
    )
    cursor = (auto.get("cursor_cli") or {})
    lines.append(
        f"**Auto-code:** {'ON' if auto.get('enabled') else 'OFF'} | cursor={'OK' if cursor.get('ok') else cursor.get('error', 'missing')}"
    )
    anomalies = d.get("anomalies") or []
    if anomalies and not anomalies[0].get("error"):
        codes = ", ".join(f"{a.get('code')}({a.get('severity')})" for a in anomalies[:5])
        lines.append(f"**Anomalies:** {codes}")
    top = ((d.get("skim") or {}).get("blocks") or {}).get("top_blocks") or []
    if top:
        lines.append(f"**Top blocks (skim):** {', '.join(f'{b[0]}={b[1]}' for b in top[:3])}")
    return "\n".join(lines)
