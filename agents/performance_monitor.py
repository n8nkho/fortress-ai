#!/usr/bin/env python3
"""Post-change monitoring — revert tunables if metrics degrade (best-effort)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.performance_analyzer import PerformanceAnalyzer


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data"


class PerformanceMonitor:
    REVERT_TRIGGERS = {
        "win_rate_drop_vs_target": 0.10,
        "max_drawdown_threshold": 0.15,
        "min_monitoring_days": 7,
    }

    def load_active_outcomes(self, *, max_entries: int = 40) -> list[dict[str, Any]]:
        p = _data_dir() / "improvement_outcomes.jsonl"
        if not p.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines()[-max_entries:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return [r for r in rows if r.get("status") == "active"]

    def get_performance_since(self, applied_iso: str) -> dict[str, Any]:
        try:
            t0 = datetime.fromisoformat(applied_iso.replace("Z", "+00:00"))
        except Exception:
            return {}
        pa = PerformanceAnalyzer()
        return pa.summarize_recent(days=max(1.0, (datetime.now(timezone.utc) - t0).total_seconds() / 86400))

    def should_revert(self, outcome: dict[str, Any]) -> bool:
        applied = outcome.get("logged_at") or outcome.get("applied_at")
        if not applied:
            return False
        try:
            t_applied = datetime.fromisoformat(str(applied).replace("Z", "+00:00"))
        except Exception:
            return False
        days = (datetime.now(timezone.utc) - t_applied).days
        if days < int(self.REVERT_TRIGGERS["min_monitoring_days"]):
            return False

        perf = self.get_performance_since(applied)
        wr = perf.get("win_rate")
        if wr is not None and wr < (0.80 - float(self.REVERT_TRIGGERS["win_rate_drop_vs_target"])):
            return True
        return False

    def revert_change(self, outcome: dict[str, Any]) -> dict[str, Any]:
        from agents.self_improvement_engine import SelfImprovementEngine

        eng = SelfImprovementEngine()
        eng.revert_last_overrides(reason=f"performance_monitor:{outcome.get('proposal_id')}")
        rev = {
            "proposal_id": outcome.get("proposal_id"),
            "reverted_at": datetime.now(timezone.utc).isoformat(),
            "reason": "performance_monitor_threshold",
        }
        p = _data_dir() / "reversions.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rev, default=str) + "\n")
        return rev

    def monitor_active_changes(self) -> list[dict[str, Any]]:
        """Check tracked outcomes; revert when triggers fire (win rate only when present)."""
        out: list[dict[str, Any]] = []
        for oc in self.load_active_outcomes():
            if self.should_revert(oc):
                out.append(self.revert_change(oc))
        return out
