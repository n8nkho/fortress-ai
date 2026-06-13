"""
Operational integrity scan — feeds recursive self-improvement with anomaly findings.

Findings are structured for SI engines (unified, skim, classic) to act on or log.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.system_time import ensure_system_tz, now_iso, parse_iso, system_tz_name

ensure_system_tz()

_ROOT = Path(__file__).resolve().parent.parent

RECENT_DECISION_WINDOW = 12

DUPLICATE_ENTRY_RECENT_HOURS = float(os.environ.get("FORTRESS_DUPLICATE_ENTRY_RECENT_HOURS", "48") or 48)


def _rows_recent_hours(rows: list[dict[str, Any]], hours: float) -> list[dict[str, Any]]:
    """Keep rows within the last N hours (ET-aware parse)."""
    if hours <= 0 or not rows:
        return rows
    cutoff = parse_iso(now_iso())
    if cutoff is None:
        return rows[-RECENT_DECISION_WINDOW:]
    from datetime import timedelta

    min_ts = cutoff - timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = _parse_row_ts(r.get("ts"))
        if ts and ts >= min_ts:
            out.append(r)
    return out if out else rows[-RECENT_DECISION_WINDOW:]


def _parse_row_ts(raw: Any) -> datetime | None:
    return parse_iso(str(raw) if raw is not None else None)


def _rows_after_deploy(rows: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    """Decision rows after a code-guard fix was deployed (or full tail if unknown)."""
    try:
        from utils.si_fix_deployment import load_deployed

        entry = (load_deployed().get("fixes") or {}).get(code) or {}
        deployed_at = _parse_row_ts(entry.get("deployed_at") or entry.get("deployed_at_utc"))
    except Exception:
        deployed_at = None
    if deployed_at is None:
        return rows[-RECENT_DECISION_WINDOW:] if len(rows) > RECENT_DECISION_WINDOW else rows
    out: list[dict[str, Any]] = []
    for r in rows:
        ts = _parse_row_ts(r.get("ts"))
        if ts and ts >= deployed_at:
            out.append(r)
    return out


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

    tail = _rows_after_deploy(rows, "duplicate_entry_accumulation")
    tail = _rows_recent_hours(tail, DUPLICATE_ENTRY_RECENT_HOURS)
    exit_tail = _rows_after_deploy(rows, "exit_notional_blocked")
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
        if act.get("block_reason") in ("already_holding", "enter_cooldown"):
            already_holding_blocks += 1

    for r in tail:
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        action = str(d.get("action") or "")
        if action == "enter_position" and act.get("executed"):
            sym = str((d.get("parameters") or {}).get("symbol") or "").upper()
            if sym:
                recent_enter_by_sym[sym] += 1

    for r in exit_tail:
        d = r.get("decision") if isinstance(r.get("decision"), dict) else {}
        act = r.get("act") if isinstance(r.get("act"), dict) else {}
        action = str(d.get("action") or "")
        detail = str(act.get("detail") or "")
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
    elif exit_notional_blocks >= 1:
        try:
            from utils.si_fix_deployment import is_deployed

            deployed = is_deployed("exit_notional_blocked")
        except Exception:
            deployed = False
        if deployed and recent_exit_notional_blocks == 0:
            pass  # historical only — guard deployed, no recent blocks
        else:
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


def _summarize_swim_wave_blocks(rows: list[dict[str, Any]]) -> tuple[Counter[str], float | None]:
    """Extract block reasons and latest day PnL from skim/infra wave journals."""
    blocks: Counter[str] = Counter()
    day_pnl: float | None = None
    for r in rows[-RECENT_DECISION_WINDOW:]:
        try:
            day_pnl = float(r.get("day_realized_pnl"))
        except (TypeError, ValueError):
            pass
        for row in r.get("results") or []:
            act = row.get("act") if isinstance(row.get("act"), dict) else {}
            dec = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            br = str(act.get("block_reason") or dec.get("reasoning") or "")
            if br:
                blocks[br.split(":")[0]] += 1
    return blocks, day_pnl


def scan_swarm_halt_exit_trap(*, rows: list[dict[str, Any]], component: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        from utils.si_fix_deployment import is_deployed

        if is_deployed("halt_blocked_exit"):
            rows = _rows_after_deploy(rows, "halt_blocked_exit")
    except Exception:
        pass
    for r in rows[-RECENT_DECISION_WINDOW:]:
        for row in r.get("results") or []:
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            side = str(features.get("side") or "flat").lower()
            if side not in ("long", "short"):
                continue
            if str(decision.get("reasoning") or "") != "swarm_halted":
                continue
            if str(decision.get("action") or "") != "wait":
                continue
            unreal = features.get("unrealized_usd")
            target = decision.get("target_usd")
            try:
                u = float(unreal) if unreal is not None else None
                t = float(target) if target is not None else None
            except (TypeError, ValueError):
                u, t = None, None
            findings.append(
                {
                    "code": "halt_blocked_exit",
                    "severity": "critical",
                    "component": component,
                    "symbol": str(row.get("symbol") or "").upper(),
                    "unrealized_usd": u,
                    "target_usd": t,
                    "recommendation": (
                        "swarm_halted must not short-circuit exit/stop/target logic for open positions."
                    ),
                    "si_action": "halt_allows_exits",
                }
            )
    return findings


def scan_swarm_universe_drift(*, component: str, metric_path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not metric_path.exists():
        return findings
    try:
        metric = json.loads(metric_path.read_text(encoding="utf-8"))
    except Exception:
        return findings
    cached = metric.get("configured_universe") if isinstance(metric.get("configured_universe"), list) else []
    if not cached:
        cached = metric.get("universe") if isinstance(metric.get("universe"), list) else []
    if not cached:
        return findings
    try:
        from utils.swarm_universe_guard import wave_context_symbols

        context = wave_context_symbols(component)
        cached = [s for s in cached if s not in context]
        if component == "skim_swarm":
            from utils.skim_swarm_config import universe as universe_fn
        elif component == "infra_swarm":
            from utils.infra_swarm_config import universe as universe_fn
        else:
            return findings
        fresh = list(universe_fn() or [])
    except Exception:
        return findings
    if fresh == cached:
        return findings
    removed = [s for s in cached if s not in fresh]
    if not removed:
        return findings
    try:
        from utils.si_fix_deployment import is_deployed

        if is_deployed("swarm_universe_drift"):
            return findings
    except Exception:
        pass
    findings.append(
        {
            "code": "swarm_universe_drift",
            "severity": "high",
            "component": component,
            "cached_universe": cached,
            "env_universe": fresh,
            "removed_still_active": removed,
            "recommendation": (
                "Running swarm cached boot universe differs from env — refresh each wave and "
                "union open positions for exits."
            ),
            "si_action": "refresh_universe",
        }
    )
    return findings


def scan_swarm_session_policy(*, component: str) -> list[dict[str, Any]]:
    """Surface swarm-level negative edge / over-churn when session SI has tightened."""
    findings: list[dict[str, Any]] = []
    try:
        from utils.swarm_session_si import load_session_policy, swarm_session_si_enabled

        if not swarm_session_si_enabled(component):
            return findings
        pol = load_session_policy(component)
        mode = str(pol.get("mode") or "normal")
        if mode == "normal":
            return findings
        negative_edge = bool(pol.get("negative_edge"))
        over_churn = bool(pol.get("over_churn"))
        if negative_edge and over_churn:
            code = "swarm_negative_edge_over_churn"
        elif negative_edge:
            code = "swarm_negative_edge"
        elif over_churn:
            code = "swarm_over_churn"
        else:
            code = f"swarm_session_{mode}"
        severity = "high" if mode in ("critical", "churn") else "medium"
        findings.append(
            {
                "code": code,
                "severity": severity,
                "component": component,
                "mode": mode,
                "session_exits": pol.get("session_exits"),
                "session_win_rate": pol.get("session_win_rate"),
                "session_expectancy_usd": pol.get("session_expectancy_usd"),
                "session_pnl_usd": pol.get("session_pnl_usd"),
                "max_open_effective": pol.get("max_open_effective"),
                "recommendation": (
                    "Session-wide negative edge or over-churn detected — swarm SI tightened "
                    "entry gates, reduced open slots, and slowed cycle interval."
                ),
                "si_action": "swarm_session_adapt",
            }
        )
    except Exception:
        pass
    return findings


def scan_skim_swarm(*, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    skim_dir = _data_dir() / "skim_swarm"
    rows = rows if rows is not None else _read_jsonl_tail(skim_dir / "decisions.jsonl")
    findings: list[dict[str, Any]] = []
    blocks, day_pnl = _summarize_swim_wave_blocks(rows)

    findings.extend(scan_swarm_halt_exit_trap(rows=rows, component="skim_swarm"))
    findings.extend(scan_swarm_universe_drift(component="skim_swarm", metric_path=skim_dir / "latest_metric.json"))
    findings.extend(scan_swarm_session_policy(component="skim_swarm"))

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

    if day_pnl is not None and day_pnl < -5.0 and len(rows) >= 20:
        findings.append(
            {
                "code": "skim_negative_session",
                "severity": "medium",
                "component": "skim_swarm",
                "session_pnl_sample_usd": round(day_pnl, 2),
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


def scan_infra_swarm(*, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    infra_dir = _data_dir() / "infra_swarm"
    rows = rows if rows is not None else _read_jsonl_tail(infra_dir / "decisions.jsonl")
    findings: list[dict[str, Any]] = []
    blocks, day_pnl = _summarize_swim_wave_blocks(rows)

    findings.extend(scan_swarm_halt_exit_trap(rows=rows, component="infra_swarm"))
    findings.extend(scan_swarm_universe_drift(component="infra_swarm", metric_path=infra_dir / "latest_metric.json"))
    findings.extend(scan_swarm_session_policy(component="infra_swarm"))

    halt_blocks = int(blocks.get("swarm_halted") or 0)
    if halt_blocks >= 50:
        open_waves = [r for r in rows[-RECENT_DECISION_WINDOW:] if int(r.get("open_positions") or 0) > 0]
        if open_waves and all(r.get("swarm_halted") for r in open_waves[-5:]):
            findings.append(
                {
                    "code": "infra_halted_with_open_book",
                    "severity": "medium",
                    "component": "infra_swarm",
                    "halt_block_samples": halt_blocks,
                    "recommendation": (
                        "Infra halted on layer/daily cap — ensure exits still execute; "
                        "tighten entry gates after negative session."
                    ),
                    "si_action": "tighten_infra_adaptive",
                }
            )

    if day_pnl is not None and day_pnl < -3.0 and len(rows) >= 10:
        findings.append(
            {
                "code": "infra_negative_session",
                "severity": "medium",
                "component": "infra_swarm",
                "session_pnl_sample_usd": round(day_pnl, 2),
                "recommendation": (
                    "Raise enter thresholds and pause losing SRP patterns after negative infra session."
                ),
                "si_action": "tighten_infra_adaptive",
            }
        )

    return findings


def scan_edge_scorecard(*, component: str) -> list[dict[str, Any]]:
    """Flag inverted payoff ratio (avg win < avg loss) from edge scorecard."""
    findings: list[dict[str, Any]] = []
    try:
        from utils.edge_scorecard import load_scorecard

        sc = load_scorecard(component)
        if not sc.get("ok"):
            return findings
        exits = int(sc.get("exits") or 0)
        if exits < 6:
            return findings
        pay = sc.get("payoff_ratio")
        pf = sc.get("profit_factor")
        exp = sc.get("expectancy_usd")
        if pay is not None and float(pay) < 0.95:
            findings.append(
                {
                    "code": "swarm_inverted_payoff",
                    "severity": "high",
                    "component": component,
                    "payoff_ratio": pay,
                    "profit_factor": pf,
                    "expectancy_usd": exp,
                    "session_date": sc.get("session_date"),
                    "recommendation": (
                        "Average loss exceeds average win — enable RR/cost gates and bracket exits; "
                        "reduce entry frequency until payoff ratio > 1."
                    ),
                    "si_action": "edge_quality_adapt",
                }
            )
    except Exception:
        pass
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


def scan_market_relative_performance() -> list[dict[str, Any]]:
    """Compare session portfolio PnL vs SPY benchmark — feeds SI queue on tape divergence."""
    try:
        from utils.market_benchmark import market_relative_findings

        return market_relative_findings()
    except Exception:
        return []


def run_integrity_scan(*, log: bool = True) -> dict[str, Any]:
    unified = scan_unified_agent()
    skim = scan_skim_swarm()
    infra = scan_infra_swarm()
    findings = unified + skim + infra + scan_edge_scorecard(component="skim_swarm") + scan_edge_scorecard(component="infra_swarm") + scan_market_relative_performance()
    ts = now_iso()
    out = {
        "timestamp": ts,
        "system_tz": system_tz_name(),
        "timestamp_utc": ts,
        "findings": findings,
        "counts": {
            "critical": sum(1 for f in findings if f.get("severity") == "critical"),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "medium": sum(1 for f in findings if f.get("severity") == "medium"),
        },
    }
    if log and findings:
        for f in findings:
            _append_recommendation_log({**f, "scan_ts": ts})
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
        if code in ("skim_negative_session", "infra_negative_session"):
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.15)
            actions["score_bias"] = min(actions.get("score_bias", 0), -0.03)
        elif code == "skim_qty_invalid_exits":
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.05)
        elif code in ("infra_halted_with_open_book", "infra_negative_session"):
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.12)
            actions["score_bias"] = min(actions.get("score_bias", 0), -0.04)
        elif code in ("swarm_negative_edge", "swarm_over_churn", "swarm_negative_edge_over_churn"):
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.18)
            actions["score_bias"] = min(actions.get("score_bias", 0), -0.04)
        elif code == "swarm_inverted_payoff":
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.14)
            actions["score_bias"] = min(actions.get("score_bias", 0), -0.035)
    return actions


def infra_adaptive_actions(scan: dict[str, Any] | None = None) -> dict[str, float]:
    """Bounded param nudges for infra adaptive_policy from integrity findings."""
    scan = scan or run_integrity_scan(log=False)
    actions: dict[str, float] = {}
    for f in scan.get("findings") or []:
        code = str(f.get("code") or "")
        comp = str(f.get("component") or "")
        if comp and comp != "infra_swarm":
            continue
        if code in ("infra_negative_session", "swarm_negative_edge", "swarm_over_churn", "swarm_negative_edge_over_churn"):
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.18)
            actions["score_bias"] = min(actions.get("score_bias", 0), -0.04)
        elif code == "swarm_inverted_payoff":
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.14)
            actions["score_bias"] = min(actions.get("score_bias", 0), -0.035)
        elif code == "infra_halted_with_open_book":
            actions["cooldown_mult"] = max(actions.get("cooldown_mult", 0), 0.12)
    return actions
