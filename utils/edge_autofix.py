"""Autonomous edge-quality fixes — applied immediately during RTH without human go."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _swarm_dir(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name


def _runtime_overrides_path(component: str) -> Path:
    return _swarm_dir(component) / "runtime_overrides.json"


def load_runtime_overrides(component: str) -> dict[str, Any]:
    p = _runtime_overrides_path(component)
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def save_runtime_overrides(component: str, doc: dict[str, Any]) -> None:
    p = _runtime_overrides_path(component)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def session_rr_margin_boost(component: str) -> float:
    """Session-scoped RR margin bump from autonomous edge autofix."""
    try:
        return max(0.0, float(load_runtime_overrides(component).get("rr_safety_margin_session_boost") or 0))
    except (TypeError, ValueError):
        return 0.0


def apply_edge_autofix(component: str, scorecard: dict[str, Any]) -> dict[str, Any]:
    """Tighten session policy and runtime knobs when payoff is inverted."""
    changes: list[str] = []
    if not scorecard.get("ok"):
        return {"component": component, "changes": changes, "skipped": "no_scorecard"}

    pay = scorecard.get("payoff_ratio")
    pf = scorecard.get("profit_factor")
    exp = scorecard.get("expectancy_usd")
    exits = int(scorecard.get("exits") or 0)
    try:
        from utils.si_capability_review import effective_edge_autofix_min_exits

        min_exits = effective_edge_autofix_min_exits()
    except Exception:
        min_exits = 4
    if exits < min_exits or pay is None:
        return {"component": component, "changes": changes, "skipped": "insufficient_exits"}

    pay_f = float(pay)
    if pay_f >= 0.95:
        return {"component": component, "changes": changes, "skipped": "payoff_ok"}

    from utils.swarm_session_si import load_session_policy, save_session_policy

    pol = load_session_policy(component)
    ov = load_runtime_overrides(component)

    pol["enter_long_delta_boost"] = round(
        min(0.15, float(pol.get("enter_long_delta_boost") or 0) + 0.02),
        4,
    )
    pol["enter_short_delta_boost"] = round(
        max(-0.15, float(pol.get("enter_short_delta_boost") or 0) - 0.02),
        4,
    )
    changes.append(f"session_enter_delta L={pol['enter_long_delta_boost']}")

    try:
        from utils.si_capability_review import effective_edge_autofix_rr_boost_cap

        rr_cap = effective_edge_autofix_rr_boost_cap()
    except Exception:
        rr_cap = 0.12
    boost = min(rr_cap, float(ov.get("rr_safety_margin_session_boost") or 0) + 0.03)
    ov["rr_safety_margin_session_boost"] = round(boost, 4)
    changes.append(f"rr_margin_boost={boost:.3f}")

    if 0.85 <= pay_f < 1.0:
        tgt_boost = min(0.12, float(ov.get("target_mult_overlay_boost") or 0) + 0.04)
        ov["target_mult_overlay_boost"] = round(tgt_boost, 4)
        changes.append(f"target_mult_boost={tgt_boost:.3f}")

    if pay_f < 0.75 or (pf is not None and float(pf) < 0.65):
        mult = min(1.45, float(pol.get("cycle_interval_mult") or 1.0) * 1.05)
        pol["cycle_interval_mult"] = round(mult, 3)
        ov["stop_mult_overlay_boost"] = round(
            min(0.08, float(ov.get("stop_mult_overlay_boost") or 0) + 0.02),
            4,
        )
        changes.append("tighten_stops_and_slow_cycle")

    if exp is not None and float(exp) < -0.03 and exits >= 6:
        mo = pol.get("max_open_effective")
        base = int(mo) if mo is not None else None
        if base is not None and base > 1:
            pol["max_open_effective"] = max(1, base - 1)
            changes.append(f"max_open={pol['max_open_effective']}")

    notes = list(pol.get("notes") or [])
    notes.append(f"edge_autofix:pay={pay_f:.2f} exp={exp} exits={exits}")
    pol["notes"] = notes[-12:]
    pol["edge_autofix_ts"] = datetime.now(timezone.utc).isoformat()
    save_session_policy(component, pol)

    toxic = _toxic_patterns(scorecard)
    if toxic:
        n = _disable_toxic_patterns(component, toxic)
        if n:
            changes.append(f"disabled_pattern_symbols={n}")

    ov["updated_utc"] = datetime.now(timezone.utc).isoformat()
    ov["last_edge_autofix"] = {
        "payoff_ratio": pay_f,
        "profit_factor": pf,
        "expectancy_usd": exp,
        "exits": exits,
        "ts": ov["updated_utc"],
    }
    save_runtime_overrides(component, ov)
    if changes:
        try:
            from utils.si_capability_review import collect_metrics
            from utils.si_intervention_log import record_intervention

            record_intervention(
                component=component,
                action="edge_autofix",
                metrics_snapshot=collect_metrics(),
                detail={"changes": changes, "payoff_ratio": pay_f},
            )
        except Exception:
            pass
    return {"component": component, "changes": changes, "toxic_patterns": toxic}


def _toxic_patterns(scorecard: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for pat, summ in (scorecard.get("by_pattern") or {}).items():
        if pat in ("?", ""):
            continue
        ex = int((summ or {}).get("exits") or 0)
        exp = (summ or {}).get("expectancy_usd")
        if ex >= 3 and exp is not None and float(exp) < -0.04:
            out.append(str(pat))
    return out


def _disable_toxic_patterns(component: str, patterns: list[str]) -> int:
    learned_dir = _swarm_dir(component) / "learned"
    if not learned_dir.is_dir():
        return 0
    n = 0
    for p in learned_dir.glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        params = doc.setdefault("params", {})
        disabled = set(params.get("disable_patterns") or [])
        added = [x for x in patterns if x not in disabled]
        if not added:
            continue
        disabled.update(added)
        params["disable_patterns"] = sorted(disabled)
        if component == "skim_swarm":
            from agents.skim_swarm.symbol_learning import save_learned

            save_learned(p.stem.upper(), doc)
        else:
            from agents.infra_swarm.symbol_learning import save_learned

            save_learned(p.stem.upper(), doc)
        n += 1
    return n


def apply_critical_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Immediate autonomous responses to critical/high edge and halt anomalies."""
    applied: list[dict[str, Any]] = []
    handled: set[str] = set()
    for f in findings:
        code = str(f.get("code") or "")
        sev = str(f.get("severity") or "")
        comp = str(f.get("component") or "")
        if sev not in ("critical", "high"):
            continue
        key = f"{comp}:{code}"
        if key in handled:
            continue
        handled.add(key)
        if code in ("halt_blocked_exit", "halt_trapped_winner"):
            res = _force_halt_exits_only(comp)
            if res:
                applied.append({"code": code, "action": res})

        elif code == "swarm_inverted_payoff":
            from utils.edge_scorecard import compute_scorecard_from_decisions, load_scorecard

            sc = load_scorecard(comp)
            if not sc.get("ok"):
                dec = _swarm_dir(comp) / "decisions.jsonl"
                sc = compute_scorecard_from_decisions(dec)
            res = apply_edge_autofix(comp, sc)
            if res.get("changes"):
                applied.append({"code": code, "action": res})

        elif code in ("swarm_negative_edge", "swarm_negative_edge_over_churn", "swarm_over_churn"):
            try:
                from utils.swarm_session_si import adapt_swarm_session

                pol = adapt_swarm_session(comp)
                applied.append({"code": code, "action": {"mode": pol.get("mode")}})
            except Exception:
                pass

        elif code == "duplicate_entry_accumulation" and comp == "unified_ai":
            res = _tighten_unified_entries()
            if res:
                applied.append({"code": code, "action": res})

    return applied


def _force_halt_exits_only(component: str) -> dict[str, Any] | None:
    if not component.endswith("_swarm"):
        component = f"{component}_swarm" if component else "skim_swarm"
    state_path = _swarm_dir(component) / "swarm_state.json"
    if not state_path.exists():
        return None
    try:
        swarm = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    swarm["halt_exits_only"] = True
    swarm["halt_exits_only_ts"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(swarm, indent=2), encoding="utf-8")
    return {"halt_exits_only": True, "component": component}


def _tighten_unified_entries() -> dict[str, Any] | None:
    """Bounded unified-agent confidence nudge via self-improvement engine."""
    try:
        from utils.si_recommendation_queue import _apply_auto_tunable

        res = _apply_auto_tunable(
            "duplicate_entry_accumulation",
            {"confidence_threshold": 0.03, "cooldown_mult": 0.1},
        )
        return res
    except Exception:
        return None


def batch_symbol_improvement(component: str, *, min_exits: int = 3) -> dict[str, Any]:
    """Force per-symbol improve_from_history on symbols bleeding this session."""
    from utils.session_loser_pause import apply_session_loser_pause

    learned_dir = _swarm_dir(component) / "learned"
    if not learned_dir.is_dir():
        return {"improved": []}
    improved: list[str] = []
    if component == "skim_swarm":
        from agents.skim_swarm.symbol_learning import improve_from_history, load_learned
    else:
        from agents.infra_swarm.symbol_learning import improve_from_history, load_learned

    for p in learned_dir.glob("*.json"):
        sym = p.stem.upper()
        try:
            doc = load_learned(sym)
        except Exception:
            continue
        stats = doc.get("session_stats") or {}
        exits = int(stats.get("exits") or 0)
        pnl = float(stats.get("sum_pnl_usd") or 0)
        if exits < min_exits or pnl >= 0:
            continue
        try:
            from utils.session_loser_pause import apply_session_loser_pause_to_params

            params = doc.setdefault("params", {})
            if apply_session_loser_pause_to_params(params, stats, component=component):
                doc["params"] = params
                if component == "skim_swarm":
                    from agents.skim_swarm.symbol_learning import save_learned

                    save_learned(sym, doc)
                else:
                    from agents.infra_swarm.symbol_learning import save_learned

                    save_learned(sym, doc)
        except Exception:
            pass
        try:
            r = improve_from_history(sym, force=True)
            if r:
                improved.append(sym)
        except Exception:
            continue
    try:
        paused = apply_session_loser_pause(component)
        if paused.get("paused"):
            improved.extend([p["symbol"] for p in paused["paused"] if p["symbol"] not in improved])
    except Exception:
        pass
    return {"component": component, "improved": improved}
