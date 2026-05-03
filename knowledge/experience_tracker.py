"""
Append-only experience log (decision + act summary) for offline analysis / future learning.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge.domain_knowledge import _repo_root


def experience_path(root: Path | None = None) -> Path:
    r = root or _repo_root()
    d = r / "data" / "domain_knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d / "experience.jsonl"


def append_experience(event: dict[str, Any], *, root: Path | None = None) -> None:
    row = {"ts_utc": datetime.now(timezone.utc).isoformat(), **event}
    p = experience_path(root)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def tail_experience(*, root: Path | None = None, max_lines: int = 20) -> list[dict[str, Any]]:
    p = experience_path(root)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-max_lines:]
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
