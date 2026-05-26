"""Track deployed code-guard fixes so integrity scans skip historical false positives."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

# Source files checked for mitigation markers per fix code
_GUARD_SOURCES: dict[str, list[Path]] = {
    "duplicate_entry_accumulation": [
        _ROOT / "agents" / "unified_ai_agent.py",
    ],
    "exit_notional_blocked": [
        _ROOT / "agents" / "unified_ai_agent.py",
        _ROOT / "agents" / "skim_swarm" / "act.py",
    ],
    "skim_qty_invalid_exits": [
        _ROOT / "agents" / "skim_swarm" / "act.py",
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
    mtimes: list[datetime] = []
    for path in paths:
        if path.is_file():
            try:
                mtimes.append(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
            except OSError:
                pass
    when = min(mtimes) if mtimes else datetime.now(timezone.utc)
    return when.isoformat()


def sync_deployed_from_registry() -> list[str]:
    """Record deployed_at for registry code guards present in source (idempotent)."""
    from utils.si_recommendation_queue import load_fix_registry

    doc = load_deployed()
    fixes = doc.setdefault("fixes", {})
    recorded: list[str] = []
    reg = load_fix_registry().get("fixes") or {}
    now = datetime.now(timezone.utc).isoformat()
    for code in reg if isinstance(reg, dict) else {}:
        meta = reg.get(code)
        if not isinstance(meta, dict) or meta.get("kind") != "code_guard":
            continue
        if not code_guard_present_in_repo(code):
            continue
        entry = fixes.get(code) if isinstance(fixes.get(code), dict) else {}
        if not entry.get("deployed_at_utc"):
            entry["deployed_at_utc"] = _estimate_deploy_time(code)
            entry["verified"] = True
            fixes[code] = entry
            recorded.append(code)
        else:
            entry["verified"] = True
            fixes[code] = entry
    doc["updated_utc"] = now
    save_deployed(doc)
    return recorded


def is_deployed(code: str) -> bool:
    doc = load_deployed()
    entry = (doc.get("fixes") or {}).get(code)
    if isinstance(entry, dict) and entry.get("deployed_at_utc"):
        return True
    return code_guard_present_in_repo(code)
