"""Dispatch SI objective/finding gaps to Tier 0/1 actions via si_gap_action_registry.json.

True SI loop: measure gaps → preferred_actions → apply → record scoreable intervention.
Never loosens pre_trade_gate or immutable caps.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from utils.system_time import now_iso

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_REGISTRY_PATH = _ROOT / "config" / "si_gap_action_registry.json"

_FORBIDDEN = frozenset(
    {
        "loosen_pre_trade_gate",
        "raise_rr_beyond_cap",
        "lower_intervention_target_min",
        "revive_classic_without_ops",
    }
)

# Objective id / queue code → registry gap key
_GAP_ALIASES = {
    "skim_payoff_ratio": "skim_payoff_ratio_gap",
    "skim_payoff_ratio_gap": "skim_payoff_ratio_gap",
    "skim_session_expectancy": "skim_session_expectancy_gap",
    "skim_session_expectancy_gap": "skim_session_expectancy_gap",
    "infra_session_expectancy": "infra_session_expectancy",
    "si_intervention_effectiveness": "si_intervention_effectiveness_gap",
    "si_intervention_effectiveness_gap": "si_intervention_effectiveness_gap",
    "classic_fill_recency": "classic_fill_recency_gap",
    "classic_fill_recency_gap": "classic_fill_recency_gap",
}


def gap_action_dispatch_enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_GAP_ACTION_DISPATCH", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def load_gap_action_registry() -> dict[str, Any]:
    if not _REGISTRY_PATH.is_file():
        return {}
    try:
        doc = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _resolve_handler(dotted: str) -> Callable[..., Any] | None:
    dotted = str(dotted or "").strip()
    if not dotted or "." not in dotted:
        return None
    mod_name, _, attr = dotted.rpartition(".")
    try:
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, attr, None)
        return fn if callable(fn) else None
    except Exception as e:
        log.warning("gap_action_handler_import_failed %s: %s", dotted, e)
        return None


def _normalize_gap_key(raw: str) -> str:
    return _GAP_ALIASES.get(str(raw or "").strip(), str(raw or "").strip())


def _gap_keys_from_inputs(
    gaps: list[dict[str, Any]] | None,
    *,
    force_codes: list[str] | None = None,
) -> list[str]:
    keys: list[str] = []
    for g in gaps or []:
        for cand in (
            g.get("objective_id"),
            g.get("code"),
            g.get("gap_code"),
        ):
            k = _normalize_gap_key(str(cand or ""))
            if k and k not in keys:
                keys.append(k)
                break
    for c in force_codes or []:
        k = _normalize_gap_key(c)
        if k and k not in keys:
            keys.append(k)
    return keys


def _invoke_action(action: str, meta: dict[str, Any], *, component: str) -> dict[str, Any]:
    if action in _FORBIDDEN:
        return {"action": action, "skipped": "forbidden"}
    handler = str(meta.get("handler") or "")
    fn = _resolve_handler(handler)
    if fn is None:
        return {"action": action, "skipped": "no_handler", "handler": handler}

    try:
        if action in ("edge_autofix",):
            from utils.edge_scorecard import compute_scorecard_from_decisions
            from utils.skim_swarm_config import swarm_data_dir as skim_dir
            from utils.infra_swarm_config import swarm_data_dir as infra_dir

            d = skim_dir() if component == "skim_swarm" else infra_dir()
            from agents.skim_swarm.eod import session_date_et

            sc = compute_scorecard_from_decisions(d / "decisions.jsonl", session_date=session_date_et())
            result = fn(component, sc if isinstance(sc, dict) else {})
        elif action in ("symbol_session_brake", "edge_autofix_exhausted"):
            # Exhausted escalates to brakes per registry.
            result = fn(component)
        elif action == "swarm_session_adapt":
            # Registry may point at adapt_session_policy; prefer adapt_swarm_session.
            try:
                from utils.swarm_session_si import adapt_swarm_session

                result = adapt_swarm_session(component)
            except Exception:
                result = fn(component) if fn.__code__.co_argcount >= 1 else fn()
        elif action == "classic_sleeve_demoted":
            result = {"marker": "classic_sleeve_demoted", "note": "demotion_policy_active"}
        else:
            # Best-effort: pass component when signature allows.
            try:
                result = fn(component)
            except TypeError:
                result = fn()
        return {"action": action, "ok": True, "result": result}
    except Exception as e:
        return {"action": action, "ok": False, "error": str(e)[:200]}


def _result_material(action: str, payload: dict[str, Any]) -> bool:
    if not payload.get("ok"):
        return False
    res = payload.get("result")
    if not isinstance(res, dict):
        return True
    if res.get("skipped"):
        # Cap-exhausted still counts as a controlled SI response when marker set.
        if str(res.get("marker") or "") == "edge_autofix_exhausted":
            return False  # not scoreable success path
        return False
    if action == "symbol_session_brake":
        return bool(res.get("brakes"))
    if action == "edge_autofix":
        return bool(res.get("changes"))
    if action.startswith("swarm_session") or action == "swarm_session_adapt":
        mode = str(res.get("mode") or "")
        return mode in ("tight", "churn", "pause") or bool(res.get("changed"))
    return True


def dispatch_gap_actions(
    gaps: list[dict[str, Any]] | None = None,
    *,
    force_codes: list[str] | None = None,
    components: tuple[str, ...] = ("skim_swarm", "infra_swarm"),
    record: bool = True,
) -> dict[str, Any]:
    """Apply preferred Tier 0/1 actions for each gap key. Returns dispatch summary."""
    if not gap_action_dispatch_enabled():
        return {"ok": False, "skipped": "gap_action_dispatch_disabled", "marker": "gap_action_dispatch"}

    reg = load_gap_action_registry()
    gap_map = reg.get("gaps") if isinstance(reg.get("gaps"), dict) else {}
    action_meta = reg.get("actions") if isinstance(reg.get("actions"), dict) else {}
    keys = _gap_keys_from_inputs(gaps, force_codes=force_codes)
    if not keys:
        return {"ok": True, "dispatched": [], "marker": "gap_action_dispatch", "note": "no_gaps"}

    dispatched: list[dict[str, Any]] = []
    scoreable_hits = 0

    for key in keys:
        spec = gap_map.get(key) if isinstance(gap_map.get(key), dict) else None
        if not spec:
            dispatched.append({"gap": key, "skipped": "unknown_gap"})
            continue
        preferred = list(spec.get("preferred_actions") or [])
        forbidden = set(spec.get("forbidden") or []) | _FORBIDDEN
        applied_for_gap: list[dict[str, Any]] = []

        for action in preferred:
            if action in forbidden:
                applied_for_gap.append({"action": action, "skipped": "forbidden"})
                continue
            meta = action_meta.get(action) if isinstance(action_meta.get(action), dict) else {}
            # Escalate exhausted → brake handler when registry says so.
            escalate = str(meta.get("escalates_to") or "")
            run_action = escalate or action
            run_meta = (
                action_meta.get(run_action)
                if escalate and isinstance(action_meta.get(run_action), dict)
                else meta
            ) or {}

            for component in components:
                # Infra expectancy gaps only touch infra; skim gaps only skim.
                if key.startswith("skim_") and component != "skim_swarm":
                    continue
                if key.startswith("infra_") and component != "infra_swarm":
                    continue
                payload = _invoke_action(run_action, run_meta, component=component)
                payload["gap"] = key
                payload["component"] = component
                applied_for_gap.append(payload)
                if _result_material(run_action, payload):
                    scoreable_hits += 1
                    if record:
                        try:
                            from utils.si_capability_review import collect_metrics
                            from utils.si_intervention_log import record_intervention

                            record_intervention(
                                component=component,
                                action=f"gap_dispatch:{run_action}",
                                metrics_snapshot=collect_metrics(),
                                detail={
                                    "marker": "gap_action_dispatch",
                                    "gap": key,
                                    "preferred_action": action,
                                    "ran": run_action,
                                    "result_summary": {
                                        k: payload.get("result", {}).get(k)
                                        if isinstance(payload.get("result"), dict)
                                        else None
                                        for k in ("marker", "brakes", "changes", "mode", "skipped")
                                    },
                                },
                                scoreable=True,
                            )
                        except Exception:
                            pass
                    # One material action per gap/component is enough this cycle.
                    break
            else:
                continue
            break  # next gap after first material preferred action

        dispatched.append({"gap": key, "actions": applied_for_gap})

    return {
        "ok": True,
        "ts": now_iso(),
        "dispatched": dispatched,
        "scoreable_hits": scoreable_hits,
        "marker": "gap_action_dispatch",
        "registry_status": reg.get("status"),
    }


def ensure_effectiveness_actions(*, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """When intervention_success_rate is None/low, force effectiveness + payoff gap dispatch."""
    force: list[str] = []
    try:
        from utils.si_intervention_log import intervention_success_rate
        from utils.si_capability_review import collect_metrics

        m = metrics if metrics is not None else collect_metrics()
        rate = intervention_success_rate(m)
        if rate is None or float(rate) < 0.35:
            force.append("si_intervention_effectiveness_gap")
        skim = m.get("skim_swarm") or {}
        pay = skim.get("rolling_payoff_ratio")
        if pay is not None and float(pay) < 1.0:
            force.append("skim_payoff_ratio_gap")
        exp = skim.get("rolling_expectancy_usd")
        if exp is not None and float(exp) < 0:
            force.append("skim_session_expectancy_gap")
    except Exception:
        force.append("si_intervention_effectiveness_gap")

    if not force:
        return {"ok": True, "skipped": "effectiveness_ok", "marker": "gap_action_dispatch"}
    return dispatch_gap_actions(force_codes=force)


__all__ = [
    "dispatch_gap_actions",
    "ensure_effectiveness_actions",
    "gap_action_dispatch_enabled",
    "load_gap_action_registry",
]
