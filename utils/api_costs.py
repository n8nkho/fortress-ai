"""DeepSeek LLM cost tracking and weekly budget enforcement for Fortress AI."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# USD per 1M tokens (aligned with Classic Fortress cost_calculator DeepSeek row)
DEEPSEEK_PRICING = {
    "deepseek-chat": {"input": 0.28, "output": 0.42},
    "deepseek-reasoner": {"input": 0.28, "output": 0.42},
}


def _data_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (root / "data")


def _ledger_path() -> Path:
    return _data_dir() / "ai_llm_cost_ledger.jsonl"


def ensure_data_dir() -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)


def estimate_llm_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    m = (model or "deepseek-chat").strip().lower()
    row = DEEPSEEK_PRICING.get(m) or DEEPSEEK_PRICING["deepseek-chat"]
    inp = (max(0, input_tokens) / 1_000_000) * row["input"]
    out = (max(0, output_tokens) / 1_000_000) * row["output"]
    return round(inp + out, 6)


def append_llm_cost_record(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "meta": meta or {},
    }
    with open(_ledger_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def week_cost_usd(now: datetime | None = None) -> tuple[float, datetime, datetime]:
    """
    Rolling cost for the current ISO week (Monday 00:00 UTC boundary).
    Returns (total_usd, week_start, week_end).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    # Monday as week start (ISO)
    weekday = now.weekday()
    week_start = (now - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    total = 0.0
    p = _ledger_path()
    if not p.exists():
        return 0.0, week_start, week_end
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    ts = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00"))
                    if ts >= week_start and ts < week_end:
                        total += float(o.get("cost_usd") or 0.0)
                except Exception:
                    continue
    except Exception:
        pass
    return round(total, 6), week_start, week_end


def weekly_budget_exceeded(now: datetime | None = None) -> tuple[bool, float, float]:
    """
    Returns (exceeded, week_spend_usd, cap_usd).
    """
    try:
        cap = float(os.environ.get("FORTRESS_AI_WEEKLY_COST_CAP_USD", "1.0"))
    except ValueError:
        cap = 1.0
    spent, _, _ = week_cost_usd(now)
    return spent >= cap, spent, cap
