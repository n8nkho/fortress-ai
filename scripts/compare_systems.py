#!/usr/bin/env python3
"""
Emit comparison metrics JSON for Classic Fortress vs Fortress AI.

Environment:
  CLASSIC_DATA_DIR — path to Classic `data/` (e.g. /home/ubuntu/trading-bot/data)
  FORTRESS_AI_DATA_DIR — path to Fortress AI `data/` (default: ../data relative to script root)

Metrics tracked (best-effort from available files):
  - opportunity_detection_rate (AI: fraction of non-wait actions in ai_metrics.jsonl)
  - win_rate, pnl_per_trade — Classic only if decisions_log / ledger present
  - decision_latency_ms — AI from ai_metrics.jsonl
  - api_cost — AI from ai_llm_cost_ledger.jsonl weekly sum
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent
_AI_ROOT = _SCRIPT.parent
sys.path.insert(0, str(_AI_ROOT))


def _read_jsonl(path: Path, limit: int = 5000) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def ai_metrics(ai_data: Path) -> dict:
    from utils.api_costs import week_cost_usd

    mpath = ai_data / "ai_metrics.jsonl"
    rows = _read_jsonl(mpath)
    non_wait = sum(1 for r in rows if r.get("opportunity_detection"))
    latencies = [float(r["decision_latency_ms"]) for r in rows if r.get("decision_latency_ms")]
    spent, wstart, wend = week_cost_usd()
    return {
        "fortress_ai": {
            "metrics_rows": len(rows),
            "opportunity_detection_rate": round(non_wait / len(rows), 4) if rows else None,
            "avg_decision_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "weekly_llm_cost_usd": spent,
            "iso_week_start_utc": wstart.isoformat(),
            "iso_week_end_utc": wend.isoformat(),
        }
    }


def classic_metrics(classic_data: Path) -> dict:
    """Best-effort from decisions_log.jsonl if present."""
    log_path = classic_data / "decisions_log.jsonl"
    rows = _read_jsonl(log_path, limit=10000)
    pnls = []
    for r in rows:
        p = r.get("pnl")
        if p is not None:
            try:
                pnls.append(float(p))
            except (TypeError, ValueError):
                pass
    wins = sum(1 for p in pnls if p > 0)
    out = {
        "classic": {
            "decisions_rows_sampled": len(rows),
            "pnl_rows": len(pnls),
            "win_rate": round(wins / len(pnls), 4) if pnls else None,
            "avg_pnl_per_closed_trade": round(sum(pnls) / len(pnls), 6) if pnls else None,
        }
    }
    return out


def main() -> None:
    raw_classic = (os.environ.get("CLASSIC_DATA_DIR") or "").strip()
    classic = Path(raw_classic).expanduser() if raw_classic else None
    ai_data = Path(os.environ.get("FORTRESS_AI_DATA_DIR", str(_AI_ROOT / "data"))).expanduser()
    merged = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "classic_data_dir": str(classic) if classic else None,
        "fortress_ai_data_dir": str(ai_data),
    }
    merged.update(ai_metrics(ai_data))
    if classic is not None and classic.is_dir():
        merged.update(classic_metrics(classic))
    print(json.dumps(merged, indent=2))


if __name__ == "__main__":
    main()
