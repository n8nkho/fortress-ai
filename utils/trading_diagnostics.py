"""Aggregate why-no-trade diagnostics from agent decision logs."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _tail_jsonl(path: Path, n: int = 400) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        raw = path.read_bytes()
        if len(raw) > 512_000:
            raw = raw[-512_000:]
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows[-n:]


def _block_reason(act: dict | None, decision: dict | None) -> str:
    if not isinstance(act, dict):
        return "unknown"
    if act.get("executed"):
        return "executed"
    detail = str(act.get("detail") or "")
    if detail:
        return detail.split(":")[0] if ":" in detail else detail
    action = (decision or {}).get("action") if isinstance(decision, dict) else None
    if action in ("wait", "screen_market", "update_beliefs"):
        return str(action)
    return "not_executed"


def _summarize_ai_decisions(rows: list[dict], *, days: int = 14) -> dict[str, Any]:
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    actions: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    executed = 0
    enter = 0
    last: dict | None = None
    for r in rows:
        ts = r.get("ts") or ""
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            continue
        if t < cut:
            continue
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        action = str(d.get("action") or r.get("action") or "unknown").lower()
        actions[action] += 1
        if action == "enter_position":
            enter += 1
        br = _block_reason(act, d)
        blocks[br] += 1
        if act.get("executed"):
            executed += 1
        last = {
            "ts": ts,
            "action": action,
            "executed": bool(act.get("executed")),
            "block_reason": br,
            "confidence": d.get("confidence"),
        }
    cycles = sum(actions.values())
    return {
        "cycles": cycles,
        "executed": executed,
        "enter_position_proposed": enter,
        "enter_execution_rate": round(executed / max(enter, 1), 4) if enter else None,
        "action_counts": dict(actions),
        "block_reason_counts": dict(blocks),
        "last_cycle": last,
    }


def _summarize_spy_decisions(rows: list[dict], *, days: int = 14) -> dict[str, Any]:
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    actions: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    executed = 0
    trade_actions = 0
    last = None
    for r in rows:
        ts = r.get("ts") or ""
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            continue
        if t < cut:
            continue
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        action = str(d.get("action") or "unknown").lower()
        actions[action] += 1
        if action in ("add_long", "add_short", "trim", "flatten_all"):
            trade_actions += 1
        br = _block_reason(act, d)
        blocks[br] += 1
        if act.get("executed"):
            executed += 1
        last = {
            "ts": ts,
            "action": action,
            "executed": bool(act.get("executed")),
            "block_reason": br,
            "confidence": d.get("confidence"),
        }
    cycles = sum(actions.values())
    return {
        "cycles": cycles,
        "executed": executed,
        "trade_actions_proposed": trade_actions,
        "trade_execution_rate": round(executed / max(trade_actions, 1), 4) if trade_actions else None,
        "action_counts": dict(actions),
        "block_reason_counts": dict(blocks),
        "last_cycle": last,
    }


def build_trading_diagnostics(*, days: int = 14) -> dict[str, Any]:
    from utils.ai_pnl_ledger import summarize_ledger
    from utils.api_costs import weekly_llm_budget_status
    from utils.spy_agent_config import dry_run as spy_dry_run, min_confidence as spy_min_conf
    from utils.spy_agent_config import spy_data_dir

    dd = _data_dir()
    ai = _summarize_ai_decisions(_tail_jsonl(dd / "ai_decisions.jsonl"), days=days)
    spy = _summarize_spy_decisions(_tail_jsonl(spy_data_dir() / "decisions.jsonl"), days=days)

    dry_ai = str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).lower() in ("1", "true", "yes")
    try:
        ai_min = float(os.environ.get("FORTRESS_AI_MIN_CONFIDENCE", "0.8"))
    except ValueError:
        ai_min = 0.8

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "lookback_days": days,
        "fortress_ai": {
            "dry_run": dry_ai,
            "min_confidence": ai_min,
            "weekly_budget": weekly_llm_budget_status(),
            "pnl_ledger": summarize_ledger(),
            **ai,
        },
        "spy_intraday": {
            "dry_run": spy_dry_run(),
            "min_confidence": spy_min_conf(),
            **spy,
        },
        "note": (
            "block_reason_counts explain why proposed trades did not reach Alpaca. "
            "confidence_below_threshold means model confidence was under min_confidence."
        ),
    }
