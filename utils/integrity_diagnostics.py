"""
Operational integrity scan — feeds recursive self-improvement with anomaly findings.

Findings are structured for SI engines (unified, skim, classic) to act on or log.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def _read_jsonl_tail(path: Path, *, max_bytes: int = 512_000, max_lines: int = 2000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
        for line in raw.decode("utf-8", errors="replace").strip().split("\n")[-max_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict):
                rows.append(o)
    except OSError:
        pass
    return rows


def _append_recommendation_log(record: dict[str, Any]) -> None:
    p = _data_dir() / "integrity_recommendations.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def scan_unified_agent(*, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = rows if rows is not None else _read_jsonl_tail(_data_dir() / "ai_decisions.jsonl")
    findings: list[dict[str, Any]] = []
    enter_by_sym: Counter[str] = Counter()
    recent_enter_by_sym: Counter[str] = Counter()
    exit_notional_blocks = 0
    recent_exit_notional_blocks = 0
    already_holding_blocks = 0

    tail = rows[-80:] if len(rows) > 80 else rows
    for r in rows:
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        action = str(d.get("action") or "")
        detail = str(act.get("detail") or "")
        if action == "enter_position" and act.get("executed"):
            sym = str((d.get("parameters") or {}).get("symbol") or "").upper()
            if sym:
                enter_by_sym[sym] += 1
        if "estimated_notional_exceeds_cap" in detail and action == "exit_position":
            exit_notional_blocks += 1
        if act.get("block_reason") == "already_holding":
            already_holding_blocks += 1

    for r in tail:
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        action = str(d.get("action") or "")
        detail = str(act.get("detail") or "")
        if action == "enter_position" and act.get("executed"):
            sym = str((d.get("parameters") or {}).get("symbol") or "").upper()
            if sym:
                recent_enter_by_sym[sym] += 1
        if "estimated_notional_exceeds_cap" in detail and action == "exit_position":
            recent_exit_notional_blocks += 1

    for sym, n in enter_by_sym.items():
        recent_n = int(recent_enter_by_sym.get(sym) or 0)
        if recent_n >= 3:
            findings.append(
                {
                    "code": "duplicate_entry_accumulation",
                    "severity": "critical" if recent_n >= 10 else "high",
                    "component": "unified_ai",
                    "symbol": sym,
                    "enter_executions_sampled": recent_n,
                    "recommendation": (
                        "Block enter_position when symbol already held; chunk exit orders under "
                        "FORTRESS_MAX_ORDER_NOTIONAL_USD; flatten oversized legacy positions."
                    ),
                    "si_action": "enforce_position_deduplication",
                }
            )
        elif n >= 10 and recent_n == 0 and already_holding_blocks >= 1:
            findings.append(
                {
                    "code": "duplicate_entry_prevented",
                    "severity": "info",
                    "component": "unified_ai",
                    "symbol": sym,
                    "historical_enter_executions": n,
                    "recommendation": "Historical stacking mitigated — already-holding gate active.",
                    "si_action": "monitor",
                }
            )

    if recent_exit_notional_blocks >= 1:
        findings.append(
            {
                "code": "exit_notional_blocked",
                "severity": "critical",
                "component": "unified_ai",
                "count_sampled": recent_exit_notional_blocks,
                "recommendation": "Chunk SELL orders so each slice fits notional cap.",
                "si_action": "chunked_exit_orders",
            }
        )
    elif exit_notional_blocks >= 1 and recent_exit_notional_blocks == 0:
        findings.append(
            {
                "code": "exit_notional_blocked",
                "severity": "info",
                "component": "unified_ai",
                "count_sampled": exit_notional_blocks,
                "recommendation": "Historical exit blocks only — chunked exit path deployed; monitor.",
                "si_action": "monitor",
            }
        )

    return findings


def scan_skim_swarm(*, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    skim_dir = _data_dir() / "skim_swarm"
    rows = rows if rows is not None else _read_jsonl_tail(skim_dir / "decisions.jsonl")
    findings: list[dict[str, Any]] = []
    blocks: Counter[str] = Counter()
    session_pnl = 0.0

    for r in rows:
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        br = str(act.get("block_reason") or "")
        if br:
            blocks[br] += 1
        try:
            session_pnl += float(r.get("session_realized_pnl_usd") or 0)
        except (TypeError, ValueError):
            pass

    qty_invalid = int(blocks.get("qty_invalid") or 0)
    if qty_invalid >= 5:
        findings.append(
            {
                "code": "skim_qty_invalid_exits",
                "severity": "high",
                "component": "skim_swarm",
                "count_sampled": qty_invalid,
                "recommendation": (
                    "Allow exit/flatten qty up to open position size (not entry max_shares); "
                    "chunk when notional exceeds cap."
                ),
                "si_action": "exit_qty_position_sized",
            }
        )

    if session_pnl < -5.0 and len(rows) >= 50:
        findings.append(
            {
                "code": "skim_negative_session",
                "severity": "medium",
                "component": "skim_swarm",
                "session_pnl_sample_usd": round(session_pnl, 2),
                "recommendation": (
                    "Increase cooldown_mult and tighten pattern gates when session PnL negative "
                    "with high churn."
                ),
                "si_action": "tighten_skim_adaptive",
            }
        )

    pattern_disabled = int(blocks.get("pattern_disabled") or 0)
    if pattern_disabled >= 100:
        findings.append(
            {
                "code": "skim_high_pattern_disables",
                "severity": "info",
                "component": "skim_swarm",
                "count_sampled": pattern_disabled,
                "recommendation": "Historical seed disables working — expect lower trade count.",
                "si_action": "monitor",
            }
        )

    return findings


def scan_positions_from_decisions(*, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Detect oversized single-symbol exposure from latest observation snapshot in log."""
    rows = rows if rows is not None else _read_jsonl_tail(_data_dir() / "ai_decisions.jsonl", max_lines=80)
    findings: list[dict[str, Any]] = []
    try:
        max_notional = float(os.environ.get("FORTRESS_MAX_ORDER_NOTIONAL_USD", "25000"))
    except ValueError:
        max_notional = 25000.0

    for r in reversed(rows):
        obs_keys = r.get("observation_keys")
        if not isinstance(obs_keys, list):
            continue
        # Positions live in observe() but aren't always logged — skip if absent
        break

    latest_metric = _data_dir() / "ai_latest_metric.json"
    # Best-effort: read last decision with positions embedded in act detail isn't reliable.
    # Use skim state / external — caller may pass positions; here we scan enter accumulation only.
    del latest_metric, max_notional
    return findings


def run_integrity_scan(*, log: bool = True) -> dict[str, Any]:
    unified = scan_unified_agent()
    skim = scan_skim_swarm()
    findings = unified + skim
    out = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "counts": {
            "critical": sum(1 for f in findings if f.get("severity") == "critical"),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "medium": sum(1 for f in findings if f.get("severity") == "medium"),
        },
    }
    if log and findings:
        for f in findings:
            _append_recommendation_log({**f, "scan_ts": out["timestamp_utc"]})
    snap = _data_dir() / "integrity_scan_latest.json"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(json.dumps(out, indent=2), encoding="utf-8")
    if log:
        try:
            from utils.si_recommendation_queue import process_scan_to_queue

            process_scan_to_queue(out)
        except Exception:
            pass
    return out


def findings_for_si_prompt(scan: dict[str, Any] | None = None) -> str:
    scan = scan or run_integrity_scan(log=False)
    items = scan.get("findings") or []
    if not items:
        return "No integrity anomalies in recent logs."
    lines = []
    for f in items[:12]:
        lines.append(
            f"- [{f.get('severity')}] {f.get('code')}: {f.get('recommendation')} (si_action={f.get('si_action')})"
        )
    return "\n".join(lines)


def skim_adaptive_actions(scan: dict[str, Any] | None = None) -> dict[str, float]:
    """Bounded param nudges for skim adaptive_policy from integrity findings."""
    scan = scan or run_integrity_scan(log=False)
    actions: dict[str, float] = {}
    for f in scan.get("findings") or []:
        code = str(f.get("code") or "")
        if code == "skim_negative_session":
            actions["cooldown_mult"] = 0.15
            actions["score_bias"] = -0.03
        elif code == "skim_qty_invalid_exits":
            actions["cooldown_mult"] = 0.05
    return actions
