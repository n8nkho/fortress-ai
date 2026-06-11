#!/usr/bin/env python3
"""Aggregate metrics from ai_decisions.jsonl for governance and monitoring."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data"


class PerformanceAnalyzer:
    """Tracks distributions and execution stats from recent decision logs."""

    def __init__(self, decisions_path: Path | None = None) -> None:
        self.decisions_path = decisions_path or (_data_dir() / "ai_decisions.jsonl")

    def load_rows_since(self, cutoff_utc: datetime, *, max_scan: int = 5000) -> list[dict[str, Any]]:
        if not self.decisions_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            raw = self.decisions_path.read_bytes()
            if len(raw) > 512_000:
                raw = raw[-512_000:]
            for line in raw.decode("utf-8", errors="replace").split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_raw = row.get("ts") or row.get("timestamp") or ""
                if not ts_raw:
                    continue
                try:
                    t = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except Exception:
                    continue
                if t >= cutoff_utc:
                    rows.append(row)
        except Exception:
            pass
        return rows[-max_scan:]

    def summarize_recent(self, *, days: float = 14, max_rows: int = 120) -> dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = self.load_rows_since(cutoff)
        if len(rows) > max_rows:
            rows = rows[-max_rows:]

        actions: list[str] = []
        confidences: list[float] = []
        exec_n = 0
        errors = 0
        variants: dict[str, int] = {}

        for r in rows:
            if r.get("error"):
                errors += 1
                continue
            d = r.get("decision")
            if not isinstance(d, dict):
                continue
            actions.append(str(d.get("action") or "?"))
            if d.get("confidence") is not None:
                try:
                    confidences.append(float(d["confidence"]))
                except (TypeError, ValueError):
                    pass
            act = r.get("act") if isinstance(r.get("act"), dict) else {}
            if act.get("executed"):
                exec_n += 1
            pv = str(d.get("prompt_variant") or "unknown")
            variants[pv] = variants.get(pv, 0) + 1

        n = len(rows)
        dist: dict[str, int] = {}
        for a in actions:
            dist[a] = dist.get(a, 0) + 1

        from utils.decision_log_metrics import (
            max_drawdown_fraction,
            trade_pnls_for_enter_executions,
        )

        tp = trade_pnls_for_enter_executions(rows)
        expectancy = (sum(tp) / len(tp)) if tp else None
        mdd = max_drawdown_fraction(tp) if len(tp) >= 2 else None

        return {
            "period_days": days,
            "decisions_count": n,
            "parse_errors": errors,
            "action_distribution": dist,
            "avg_confidence": (sum(confidences) / len(confidences)) if confidences else None,
            "execution_rate": (exec_n / n) if n else 0.0,
            "by_prompt_variant": variants,
            "rolling_expectancy_usd": round(expectancy, 4) if expectancy is not None else None,
            "max_drawdown_fraction": round(mdd, 4) if mdd is not None else None,
            "pnl_sample_trades": len(tp),
            "note": "Expectancy-first monitor metrics from enter rows with PnL when present.",
        }
