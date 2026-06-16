"""SI intervention attribution — measure whether actions improved outcomes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.system_time import now_iso

# Heartbeat / no-op actions must not count as interventions for success-rate scoring.
_NO_OP_ACTIONS = frozenset({"swarm_session_normal"})


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def intervention_log_path() -> Path:
    return _data_dir() / "si_capability" / "interventions.jsonl"


def record_intervention(
    *,
    component: str,
    action: str,
    metrics_snapshot: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    p = intervention_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": now_iso(),
        "component": component,
        "action": action,
        "metrics_snapshot": metrics_snapshot or {},
        "detail": detail or {},
        "markers": ["si_intervention_recorded"],
    }
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _read_tail(path: Path, *, max_bytes: int = 256_000) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
            if isinstance(doc, dict):
                out.append(doc)
        except Exception:
            continue
    return out


def _expectancy_usd(comp_metrics: dict[str, Any]) -> float | None:
    """Prefer rolling expectancy; fall back to session when rolling window is empty."""
    for key in ("rolling_expectancy_usd", "session_expectancy_usd"):
        val = comp_metrics.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def intervention_success_rate(
    metrics: dict[str, Any],
    *,
    lookback: int = 12,
) -> float | None:
    """Fraction of recent actionable interventions followed by improved expectancy."""
    rows = _read_tail(intervention_log_path())[-lookback:]
    if not rows:
        return None

    improved = 0
    scored = 0
    for row in rows:
        action = str(row.get("action") or "")
        if action in _NO_OP_ACTIONS:
            continue
        comp = str(row.get("component") or "")
        if not comp or comp == "si_meta":
            continue
        before = (row.get("metrics_snapshot") or {}).get(comp) or {}
        before_exp = _expectancy_usd(before)
        after_exp = _expectancy_usd(metrics.get(comp) or {})
        if before_exp is None or after_exp is None:
            continue
        scored += 1
        if after_exp > before_exp + 1e-9:
            improved += 1
    if not scored:
        return None
    return improved / scored
