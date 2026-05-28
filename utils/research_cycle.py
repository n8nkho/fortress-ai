"""Research cycle — hypothesis promote/kill from scenario stress + adversarial replay."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.skim_swarm.scenario_stress import (
    TradeRecord,
    _default_overlay,
    load_trades_from_decisions,
    stress_universe,
)
from utils.adversarial_replay import adversarial_improves_vs_baseline, score_overlay_adversarial
from utils.movement_anticipation import compute_movement_anticipation, research_state_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def hypothesis_registry_path() -> Path:
    return _repo_root() / "config" / "hypothesis_registry.json"


def load_hypothesis_registry() -> dict[str, Any]:
    p = hypothesis_registry_path()
    if not p.exists():
        return {"version": 1, "hypotheses": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_hypothesis_registry(doc: dict[str, Any]) -> None:
    hypothesis_registry_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def load_research_state(component: str) -> dict[str, Any]:
    p = research_state_path(component)
    if not p.exists():
        return {"promoted": [], "killed": [], "shadow": [], "last_cycle_ts": None, "results": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"promoted": [], "killed": [], "shadow": [], "last_cycle_ts": None, "results": {}}


def save_research_state(component: str, state: dict[str, Any]) -> None:
    p = research_state_path(component)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_entry_snapshots(
    decisions_path: Path,
    *,
    max_sessions: int = 5,
) -> list[dict[str, Any]]:
    """Entry rows with features for anticipation counterfactual tests."""
    if not decisions_path.exists():
        return []
    trades, sessions = load_trades_from_decisions(decisions_path, max_sessions=max_sessions)
    keep_sessions = set(sessions[-max_sessions:]) if max_sessions > 0 else set(sessions)
    trade_keys = {(t.session_date, t.symbol, t.side, round(t.entry_score, 4)) for t in trades}

    out: list[dict[str, Any]] = []
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            wave = json.loads(line)
        except json.JSONDecodeError:
            continue
        from agents.skim_swarm.pnl import _wave_session_date

        sess = _wave_session_date(str(wave.get("ts") or ""))
        if sess not in keep_sessions:
            continue
        for row in wave.get("results") or []:
            act = row.get("act") or {}
            dec = row.get("decision") or {}
            if not act.get("executed"):
                continue
            action = str(dec.get("action") or "").lower()
            if action not in ("enter_long", "enter_short"):
                continue
            sym = str(row.get("symbol") or "").upper()
            side = "long" if "long" in action else "short"
            score = float(dec.get("score") or 0)
            features = row.get("features") if isinstance(row.get("features"), dict) else {}
            feat_full = {
                "symbol": sym,
                "r1m": features.get("r1m"),
                "r3m": features.get("r3m"),
                "r5m": features.get("r5m"),
                "rsi1m": features.get("rsi1m"),
                "residual_vs_spy": features.get("residual_vs_spy"),
                "spy_r5m": features.get("spy_r5m"),
                "vix_last": features.get("vix_last"),
            }
            exit_pnl = None
            exit_reason = None
            for t in trades:
                if t.session_date == sess and t.symbol == sym and t.side == side:
                    if abs(t.entry_score - score) < 0.02:
                        exit_pnl = t.exit_pnl
                        exit_reason = t.exit_reason
                        break
            out.append(
                {
                    "session_date": sess,
                    "symbol": sym,
                    "side": side,
                    "entry_score": score,
                    "features": feat_full,
                    "exit_pnl": exit_pnl,
                    "exit_reason": exit_reason,
                }
            )
    return out


def _would_block_entry(
    hypothesis_id: str,
    side: str,
    features: dict[str, Any],
    *,
    component: str,
) -> bool:
    promoted = {hypothesis_id}
    ant = compute_movement_anticipation(features, component=component, promoted_hypotheses=promoted)
    if side == "long" and ant.get("block_long"):
        return True
    if side == "short" and ant.get("block_short"):
        return True
    return False


def evaluate_anticipation_hypothesis(
    hypothesis_id: str,
    entries: list[dict[str, Any]],
    *,
    component: str = "skim_swarm",
    tests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tests = tests or {}
    min_loser = float(tests.get("min_blocked_loser_pnl_usd") or 0.2)
    max_winner = float(tests.get("max_blocked_winner_pnl_usd") or 0.35)

    blocked_loser_pnl = 0.0
    blocked_winner_pnl = 0.0
    blocked = 0
    for row in entries:
        pnl = row.get("exit_pnl")
        if pnl is None:
            continue
        side = str(row.get("side") or "long")
        feats = row.get("features") if isinstance(row.get("features"), dict) else {}
        if not _would_block_entry(hypothesis_id, side, feats, component=component):
            continue
        blocked += 1
        pnl_f = float(pnl)
        if pnl_f < 0:
            blocked_loser_pnl += abs(pnl_f)
        else:
            blocked_winner_pnl += pnl_f

    net = blocked_loser_pnl - blocked_winner_pnl
    passes = blocked_loser_pnl >= min_loser and blocked_winner_pnl <= max_winner and net > 0
    return {
        "hypothesis_id": hypothesis_id,
        "kind": "anticipation_gate",
        "blocked_entries": blocked,
        "blocked_loser_pnl_usd": round(blocked_loser_pnl, 4),
        "blocked_winner_pnl_usd": round(blocked_winner_pnl, 4),
        "net_improvement_usd": round(net, 4),
        "passes": passes,
    }


def evaluate_param_hypothesis(
    hypothesis_id: str,
    trades: list[TradeRecord],
    scenario_report: dict[str, Any],
    *,
    tests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tests = tests or {}
    min_adv = float(tests.get("adversarial_min_improvement_usd") or 0.05)

    if hypothesis_id == "scenario_stress_overlay":
        best_delta = -999.0
        tested = 0
        for row in scenario_report.get("symbols") or []:
            if not row.get("apply_recommended") or not row.get("recommended_params"):
                continue
            sym = str(row.get("symbol") or "")
            ov = dict(row["recommended_params"])
            sym_trades = [t for t in trades if t.symbol == sym]
            if len(sym_trades) < 2:
                continue
            tested += 1
            res = adversarial_improves_vs_baseline(sym_trades, ov, min_improvement_usd=min_adv)
            best_delta = max(best_delta, res["delta_usd"])
        passes = tested > 0 and best_delta >= min_adv
        return {
            "hypothesis_id": hypothesis_id,
            "passes": passes,
            "symbols_tested": tested,
            "best_adversarial_delta": best_delta if tested else None,
        }

    if hypothesis_id == "adversarial_tighten_entries":
        overlay = _default_overlay(enter_long_delta=0.03, enter_short_delta=-0.03)
        res = adversarial_improves_vs_baseline(trades, overlay, min_improvement_usd=min_adv)
        exp = res["overlay_pnl"] / max(res["overlay_exits"], 1)
        min_exp = float(tests.get("min_expectancy_usd") or -0.05)
        passes = res["passes"] and exp >= min_exp
        return {
            "hypothesis_id": hypothesis_id,
            "passes": passes,
            "adversarial": res,
            "expectancy_usd": round(exp, 4),
        }

    return {"hypothesis_id": hypothesis_id, "passes": False, "reason": "unknown_param_hypothesis"}


def run_research_cycle(
    *,
    component: str = "skim_swarm",
    max_sessions: int = 5,
    decisions_path: Path | None = None,
    apply_promoted: bool = False,
) -> dict[str, Any]:
    from utils.skim_swarm_config import swarm_data_dir

    data_dir = swarm_data_dir() if component == "skim_swarm" else _data_dir() / component
    decisions_path = decisions_path or (data_dir / "decisions.jsonl")

    registry = load_hypothesis_registry()
    state = load_research_state(component)
    promoted: set[str] = set(state.get("promoted") or [])
    killed: set[str] = set(state.get("killed") or [])

    scenario_report = stress_universe(
        max_sessions=max_sessions,
        decisions_path=decisions_path,
        out_path=data_dir / "scenario_stress_report.json",
    )
    trades, sessions = load_trades_from_decisions(decisions_path, max_sessions=max_sessions)
    entries = load_entry_snapshots(decisions_path, max_sessions=max_sessions)

    baseline_adv = score_overlay_adversarial(trades, _default_overlay())
    results: dict[str, Any] = {
        "scenario_stress": {"trade_count": len(trades), "sessions": sessions},
        "adversarial_baseline_pnl": baseline_adv.sum_pnl_usd,
    }

    newly_promoted: list[str] = []
    newly_killed: list[str] = []

    for hyp in registry.get("hypotheses") or []:
        hid = str(hyp.get("id") or "")
        if not hid or hid in killed:
            continue
        kind = str(hyp.get("kind") or "")
        tests = hyp.get("tests") if isinstance(hyp.get("tests"), dict) else {}

        if kind == "anticipation_gate":
            ev = evaluate_anticipation_hypothesis(hid, entries, component=component, tests=tests)
        elif kind == "param_overlay":
            ev = evaluate_param_hypothesis(hid, trades, scenario_report, tests=tests)
        else:
            ev = {"hypothesis_id": hid, "passes": False, "reason": "unknown_kind"}

        results[hid] = ev
        if ev.get("passes"):
            if hid not in promoted:
                newly_promoted.append(hid)
            promoted.add(hid)
            hyp["status"] = "promoted"
        else:
            hyp["status"] = "shadow" if hid not in killed else "killed"

    if apply_promoted and newly_promoted:
        from agents.skim_swarm.scenario_stress import apply_scenario_stress_to_learned

        if "scenario_stress_overlay" in promoted:
            apply_scenario_stress_to_learned(scenario_report)

    state = {
        "promoted": sorted(promoted),
        "killed": sorted(killed),
        "shadow": sorted(
            h["id"]
            for h in registry.get("hypotheses") or []
            if h.get("id") not in promoted and h.get("id") not in killed
        ),
        "last_cycle_ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "newly_promoted": newly_promoted,
        "newly_killed": newly_killed,
    }
    save_research_state(component, state)
    save_hypothesis_registry(registry)

    report = {
        "ok": True,
        "ts": state["last_cycle_ts"],
        "component": component,
        "max_sessions": max_sessions,
        "promoted": state["promoted"],
        "newly_promoted": newly_promoted,
        "hypothesis_results": results,
        "scenario_apply_if_promoted": "scenario_stress_overlay" in promoted and apply_promoted,
    }
    out_path = data_dir / "research_cycle_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")
