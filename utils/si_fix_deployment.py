"""Track deployed code-guard fixes so integrity scans skip historical false positives."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.system_time import ensure_system_tz, now, now_iso, parse_iso, system_tz_name

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent

# Source files checked for mitigation markers per fix code
_GUARD_SOURCES: dict[str, list[Path]] = {
    "duplicate_entry_accumulation": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "utils" / "unified_enter_guard.py",
        _ROOT / "utils" / "order_chunking.py",
        _ROOT / "config" / "defaults.py",
        _ROOT / "unified_ai" / "position_manager.py",
        _ROOT / "unified_ai" / "order_executor.py",
        _ROOT / "unified_ai" / "legacy_flattener.py",
        _ROOT / "unified_ai" / "risk_controller.py",
    ],
    "exit_notional_blocked": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "skim_swarm" / "act.py",
    ],
    "skim_qty_invalid_exits": [
        _ROOT / "agents" / "skim_swarm" / "act.py",
    ],
    "halt_blocked_exit": [
        _ROOT / "agents" / "skim_swarm" / "signal.py",
        _ROOT / "agents" / "infra_swarm" / "signal.py",
    ],
    "swarm_universe_drift": [
        _ROOT / "agents" / "skim_swarm_agent.py",
        _ROOT / "agents" / "infra_swarm_agent.py",
        _ROOT / "utils" / "swarm_runtime.py",
    ],
    "swarm_orphan_symbol_entry": [
        _ROOT / "utils" / "swarm_universe_guard.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
    ],
    "alpaca_bracket_tick_violation": [
        _ROOT / "utils" / "edge_quality.py",
        _ROOT / "utils" / "alpaca_execution.py",
    ],
    "swarm_critical_pause_entries": [
        _ROOT / "utils" / "swarm_session_si.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
        _ROOT / "agents" / "infra_swarm" / "signal.py",
    ],
    "unified_off_denylist_watchlist": [
        _ROOT / "utils" / "unified_symbol_pool.py",
        _ROOT / "agents" / "unified_ai_agent.py",
    ],
    "edge_rr_cost_gates": [
        _ROOT / "utils" / "edge_quality.py",
        _ROOT / "agents" / "skim_swarm" / "signal.py",
    ],
    "swarm_pnl_decisions_sync": [
        _ROOT / "utils" / "swarm_decisions_pnl.py",
        _ROOT / "utils" / "swarm_pnl_ledger.py",
        _ROOT / "agents" / "skim_swarm" / "symbol_learning.py",
    ],
}


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def deployed_path() -> Path:
    return _data_dir() / "si_deployed_fixes.json"


def load_deployed() -> dict[str, Any]:
    p = deployed_path()
    if not p.exists():
        return {"fixes": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"fixes": {}}
    except Exception:
        return {"fixes": {}}


def save_deployed(doc: dict[str, Any]) -> None:
    deployed_path().parent.mkdir(parents=True, exist_ok=True)
    doc.setdefault("system_tz", system_tz_name())
    deployed_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def code_guard_present_in_repo(code: str) -> bool:
    from utils.si_recommendation_queue import load_fix_registry

    reg = load_fix_registry().get("fixes") or {}
    meta = reg.get(code) if isinstance(reg, dict) else None
    if not isinstance(meta, dict):
        return False
    markers = [str(m) for m in (meta.get("mitigation_markers") or []) if m]
    if not markers:
        return False
    paths = _GUARD_SOURCES.get(code) or list(_ROOT.glob("agents/**/*.py"))
    blob = ""
    for path in paths:
        if path.is_file():
            try:
                blob += path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    if not blob:
        return False
    return all(m in blob for m in markers)


def _estimate_deploy_time(code: str) -> str:
    paths = _GUARD_SOURCES.get(code) or []
    tz = now().tzinfo
    mtimes: list[datetime] = []
    for path in paths:
        if path.is_file():
            try:
                mtimes.append(datetime.fromtimestamp(path.stat().st_mtime, tz=tz))
            except OSError:
                pass
    when = min(mtimes) if mtimes else now()
    return when.isoformat()


def _deployed_at(entry: dict[str, Any]) -> str | None:
    raw = entry.get("deployed_at") or entry.get("deployed_at_utc")
    return str(raw) if raw else None


def sync_deployed_from_registry() -> list[str]:
    """Record deployed_at for registry code guards present in source (idempotent)."""
    from utils.si_recommendation_queue import load_fix_registry

    doc = load_deployed()
    fixes = doc.setdefault("fixes", {})
    recorded: list[str] = []
    reg = load_fix_registry().get("fixes") or {}
    now_ts = now_iso()
    for code in reg if isinstance(reg, dict) else {}:
        meta = reg.get(code)
        if not isinstance(meta, dict) or meta.get("kind") != "code_guard":
            continue
        if not code_guard_present_in_repo(code):
            continue
        entry = fixes.get(code) if isinstance(fixes.get(code), dict) else {}
        if not _deployed_at(entry):
            ts = _estimate_deploy_time(code)
            entry["deployed_at"] = ts
            entry["deployed_at_utc"] = ts
            entry["verified"] = True
            fixes[code] = entry
            recorded.append(code)
        else:
            entry.setdefault("deployed_at", _deployed_at(entry))
            entry.setdefault("deployed_at_utc", entry.get("deployed_at"))
            fixes[code] = entry
    doc["updated_at"] = now_ts
    doc["system_tz"] = system_tz_name()
    save_deployed(doc)
    return recorded


def is_deployed(code: str) -> bool:
    entry = (load_deployed().get("fixes") or {}).get(code)
    if isinstance(entry, dict) and _deployed_at(entry):
        return True
    return code_guard_present_in_repo(code)


def deployed_at(code: str) -> datetime | None:
    entry = (load_deployed().get("fixes") or {}).get(code)
    if not isinstance(entry, dict):
        return None
    return parse_iso(_deployed_at(entry))
