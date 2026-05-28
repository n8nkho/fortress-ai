"""Post-wave swarm health — continuous SI anomaly detection and auto-correction."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.swarm_runtime import open_position_symbols, refresh_universe_if_changed


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _swarm_health_path(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name / "swarm_health.json"


def load_swarm_health(component: str) -> dict[str, Any]:
    p = _swarm_health_path(component)
    if not p.exists():
        return {"anomalies": [], "last_wave": None}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"anomalies": [], "last_wave": None}
    except Exception:
        return {"anomalies": [], "last_wave": None}


def save_swarm_health(component: str, doc: dict[str, Any]) -> None:
    p = _swarm_health_path(component)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _target_from_decision(decision: dict[str, Any]) -> float | None:
    try:
        return float(decision.get("target_usd"))
    except (TypeError, ValueError):
        return None


def detect_wave_anomalies(
    *,
    component: str,
    wave: int,
    swarm_halted: bool,
    results: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    cached_universe: list[str],
    fresh_universe: list[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    open_syms = set(open_position_symbols(positions))

    drift_added = [s for s in fresh_universe if s not in cached_universe]
    drift_removed = [s for s in cached_universe if s not in fresh_universe]
    if drift_added or drift_removed:
        findings.append(
            {
                "code": "swarm_universe_drift",
                "severity": "high" if drift_removed else "medium",
                "component": component,
                "added": drift_added,
                "removed": drift_removed,
                "recommendation": "Refresh wave symbol list from current env universe.",
                "si_action": "refresh_universe",
            }
        )

    orphans = [s for s in open_syms if s not in cached_universe]
    if orphans:
        findings.append(
            {
                "code": "swarm_orphan_open_position",
                "severity": "high",
                "component": component,
                "symbols": sorted(orphans),
                "recommendation": "Include open broker positions in wave cycle for exit management.",
                "si_action": "union_open_positions",
            }
        )

    for row in results:
        sym = str(row.get("symbol") or "").upper()
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        side = str(features.get("side") or "flat").lower()
        reasoning = str(decision.get("reasoning") or "")
        action = str(decision.get("action") or "wait")
        unreal = features.get("unrealized_usd")
        target = _target_from_decision(decision)

        if side not in ("long", "short") or unreal is None or target is None:
            continue

        u = float(unreal)
        if reasoning == "swarm_halted" and action == "wait":
            findings.append(
                {
                    "code": "halt_blocked_exit",
                    "severity": "critical",
                    "component": component,
                    "symbol": sym,
                    "unrealized_usd": round(u, 4),
                    "target_usd": round(target, 4),
                    "recommendation": "Halt must block entries only; exits/stops must still run.",
                    "si_action": "halt_allows_exits",
                }
            )
        elif swarm_halted and u >= target and action == "wait":
            findings.append(
                {
                    "code": "halt_trapped_winner",
                    "severity": "critical",
                    "component": component,
                    "symbol": sym,
                    "unrealized_usd": round(u, 4),
                    "target_usd": round(target, 4),
                    "recommendation": "Open winner at target while halted — exit path blocked.",
                    "si_action": "halt_allows_exits",
                }
            )

    if swarm_halted and open_syms:
        blocked_exits = sum(
            1
            for f in findings
            if f.get("code") in ("halt_blocked_exit", "halt_trapped_winner")
        )
        if blocked_exits == 0 and wave % 20 == 0:
            findings.append(
                {
                    "code": "swarm_halted_with_open",
                    "severity": "info",
                    "component": component,
                    "open_count": len(open_syms),
                    "recommendation": "Swarm halted for new entries; monitoring open book for exits.",
                    "si_action": "monitor",
                }
            )

    return findings


def run_wave_health(
    *,
    component: str,
    wave: int,
    swarm_halted: bool,
    results: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    cached_universe: list[str],
    universe_fn,
    day_realized_pnl: float | None = None,
) -> dict[str, Any]:
    """Analyze wave, persist health snapshot, trigger integrity scan on critical findings."""
    fresh, drift_event = refresh_universe_if_changed(cached_universe, universe_fn)
    findings = detect_wave_anomalies(
        component=component,
        wave=wave,
        swarm_halted=swarm_halted,
        results=results,
        positions=positions,
        cached_universe=cached_universe,
        fresh_universe=fresh,
    )

    health = load_swarm_health(component)
    health["last_wave"] = wave
    health["updated_utc"] = datetime.now(timezone.utc).isoformat()
    health["last_findings"] = findings
    if findings:
        hist = health.setdefault("anomalies", [])
        for f in findings:
            if f.get("severity") in ("critical", "high"):
                hist.append({**f, "wave": wave, "ts": health["updated_utc"]})
        health["anomalies"] = hist[-50:]
    save_swarm_health(component, health)

    critical = [f for f in findings if f.get("severity") == "critical"]
    if critical:
        try:
            from utils.integrity_diagnostics import run_integrity_scan

            run_integrity_scan(log=True)
        except Exception:
            pass

    try:
        from utils.swarm_session_si import adapt_swarm_session

        session_policy = adapt_swarm_session(component, day_realized_pnl=day_realized_pnl)
    except Exception:
        session_policy = None

    return {
        "findings": findings,
        "universe_drift": drift_event,
        "fresh_universe": fresh,
        "session_policy": session_policy,
    }


def record_halt_exit_trap(
    *,
    component: str,
    symbol: str,
    features: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    """Per-symbol hook when halt incorrectly blocks an open position."""
    side = str(features.get("side") or "flat").lower()
    if side not in ("long", "short"):
        return
    reasoning = str(decision.get("reasoning") or "")
    if reasoning != "swarm_halted":
        return
    health = load_swarm_health(component)
    traps = health.setdefault("halt_exit_traps", {})
    sym = symbol.upper()
    traps[sym] = int(traps.get(sym) or 0) + 1
    health["halt_exit_traps"] = traps
    save_swarm_health(component, health)
