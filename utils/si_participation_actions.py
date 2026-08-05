"""SI participation policy — true SI acts on strong-tape / deep-lag situations.

Strategies (never loosens pre_trade_gate or MR rails):
1. Deep lag (alpha << deep floor on strong tape): wait + denylist audit; no tape override.
2. Alpha recovered: selective denylist thaw of high-rolling-expectancy universe names.
3. Strong tape, 0 exits, not deep-lagging, near-threshold scores: infra soft entry (once/session).
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from utils.system_time import now, now_iso

log = logging.getLogger(__name__)

_MARKER_DEEP = "si_deep_lag_wait"
_MARKER_DENY = "si_denylist_audit"
_MARKER_THAW = "si_denylist_thaw"
_MARKER_INFRA = "si_infra_strong_tape_soft"
_MARKER_ROLLOVER = "si_participation_session_rollover"

_SCORE_RE = re.compile(r"no_entry\s+score=(-?\d+(?:\.\d+)?)", re.I)


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _policy_path() -> Path:
    return _data_dir() / "si_capability" / "participation_policy.json"


def _enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_PARTICIPATION_POLICY", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _load_policy() -> dict[str, Any]:
    p = _policy_path()
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _save_policy(doc: dict[str, Any]) -> None:
    p = _policy_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def ensure_participation_policy_session() -> dict[str, Any]:
    """Roll participation_policy.json to the current ET session (clear stale strategies)."""
    today = now().date().isoformat()
    pol = _load_policy()
    if str(pol.get("session_date_et") or "") == today:
        return pol
    prev = str(pol.get("session_date_et") or "") or None
    pol = {
        "session_date_et": today,
        "events": [],
        "strategy": None,
        "rollover_from": prev,
        "marker": _MARKER_ROLLOVER,
        "updated_utc": now_iso(),
    }
    _save_policy(pol)
    log.info("%s from=%s to=%s", _MARKER_ROLLOVER, prev, today)
    return pol


def _portfolio() -> dict[str, Any]:
    try:
        from utils.market_benchmark import build_portfolio_session_metrics

        return build_portfolio_session_metrics() or {}
    except Exception:
        return {}


def _deep_floor() -> float:
    try:
        from utils.portfolio_session.constructive_tape_override import deep_alpha_floor

        return float(deep_alpha_floor())
    except Exception:
        return -1.0


def _soft_alpha() -> float:
    try:
        from utils.portfolio_session.constructive_tape_override import soft_alpha_threshold

        return float(soft_alpha_threshold())
    except Exception:
        return -0.8


def _denylist_sources(cfg: Any) -> dict[str, Any]:
    """Break out file / pause / env denylist so audit sees what signal uses for manual_denylist."""
    ov = cfg.runtime_overrides() if hasattr(cfg, "runtime_overrides") else {}
    if not isinstance(ov, dict):
        ov = {}
    file_raw = ov.get("denylist_symbols") or []
    file_syms = sorted({cfg.normalize_symbol(str(x)) for x in file_raw if str(x).strip()})
    review = ov.get("review_actions") if isinstance(ov.get("review_actions"), dict) else {}
    pause_raw = review.get("pause_symbols") or ov.get("pause_symbols") or []
    pause_syms = sorted({cfg.normalize_symbol(str(x)) for x in pause_raw if str(x).strip()})
    mod = str(getattr(cfg, "__name__", "") or "")
    env_key = "FORTRESS_INFRA_DENYLIST" if "infra_swarm" in mod else "FORTRESS_SKIM_DENYLIST"
    env_raw = (os.environ.get(env_key) or "").strip()
    env_syms = sorted({cfg.normalize_symbol(x) for x in env_raw.split(",") if x.strip()})
    return {"file": file_syms, "pause": pause_syms, "env": env_syms, "env_key": env_key}


def _decision_denylist_symbols(component: str, *, today: str) -> list[str]:
    """Symbols blocked with manual_denylist/denylist in today's decisions (actual gate path)."""
    sub = "skim_swarm" if "skim" in component else "infra_swarm"
    path = _data_dir() / sub / "decisions.jsonl"
    if not path.is_file():
        return []
    found: set[str] = set()
    try:
        raw = path.read_bytes()[-400_000:]
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = str(row.get("ts") or "")
            sess = str(row.get("session_date_et") or "")
            if today not in ts and sess != today:
                continue
            dec = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            act = row.get("act") if isinstance(row.get("act"), dict) else {}
            br = str(act.get("block_reason") or dec.get("reasoning") or "")
            if br != "manual_denylist" and "denylist" not in br.lower():
                continue
            sym = str(row.get("symbol") or dec.get("symbol") or "").strip().upper()
            if sym:
                found.add(sym)
            # wave decisions may nest per-symbol results
            for item in row.get("wave") or row.get("symbols") or []:
                if not isinstance(item, dict):
                    continue
                ibr = str(
                    (item.get("act") or {}).get("block_reason")
                    or (item.get("decision") or {}).get("reasoning")
                    or item.get("block_reason")
                    or ""
                )
                if ibr != "manual_denylist" and "denylist" not in ibr.lower():
                    continue
                s2 = str(item.get("symbol") or "").strip().upper()
                if s2:
                    found.add(s2)
    except Exception:
        pass
    return sorted(found)


def _session_pause_entry_symbols(component: str, *, today: str) -> list[str]:
    sub = "skim_swarm" if "skim" in component else "infra_swarm"
    learned = _data_dir() / sub / "learned"
    if not learned.is_dir():
        return []
    out: list[str] = []
    for f in learned.glob("*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(doc.get("session_date_et") or "") != today:
            continue
        params = doc.get("params") or {}
        if params.get("pause_entries"):
            out.append(f.stem.upper().replace("_", "."))
    return sorted(set(out))


def audit_denylist_vs_universe() -> dict[str, Any]:
    """Compare skim/infra denylists (file/pause/env + decision trail) to active universes."""
    from utils import skim_swarm_config as skim_cfg
    from utils import infra_swarm_config as infra_cfg

    today = now().date().isoformat()
    skim_sources = _denylist_sources(skim_cfg)
    infra_sources = _denylist_sources(infra_cfg)
    skim_deny = set(skim_cfg.runtime_denylist())
    infra_deny = set(infra_cfg.runtime_denylist())
    try:
        skim_uni = {skim_cfg.normalize_symbol(x) for x in skim_cfg.universe()}
    except Exception:
        skim_uni = set()
    try:
        infra_uni = {infra_cfg.normalize_symbol(x) for x in infra_cfg.universe()}
    except Exception:
        infra_uni = set()

    skim_decision = _decision_denylist_symbols("skim_swarm", today=today)
    infra_decision = _decision_denylist_symbols("infra_swarm", today=today)
    skim_pause_learned = _session_pause_entry_symbols("skim_swarm", today=today)
    infra_pause_learned = _session_pause_entry_symbols("infra_swarm", today=today)

    # Union that matches signal gate + observed blocks + session brakes.
    skim_effective = skim_deny | set(skim_decision)
    infra_effective = infra_deny | set(infra_decision)

    skim_overlap = sorted(skim_effective & skim_uni)
    infra_overlap = sorted(infra_effective & infra_uni)

    candidates: list[dict[str, Any]] = []
    for component, symbols in (("skim_swarm", skim_overlap), ("infra_swarm", infra_overlap)):
        learned_dir = _data_dir() / component / "learned"
        for sym in symbols:
            if sym in (skim_pause_learned if component == "skim_swarm" else infra_pause_learned):
                continue  # never thaw SI pause brakes via denylist thaw
            doc: dict[str, Any] = {}
            f = learned_dir / f"{sym.lower().replace('.', '_')}.json"
            if not f.is_file():
                f = learned_dir / f"{sym}.json"
            if f.is_file():
                try:
                    doc = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    doc = {}
            st = doc.get("session_stats") or {}
            lt = doc.get("lifetime") or doc.get("lifetime_stats") or {}
            exp = None
            for src in (st, lt):
                exits = int(src.get("exits") or 0)
                pnl = float(src.get("sum_pnl_usd") or 0)
                if exits > 0:
                    exp = pnl / exits
                    break
            params = doc.get("params") or {}
            if params.get("pause_entries"):
                continue
            if exp is not None and exp > 0:
                candidates.append(
                    {
                        "symbol": sym,
                        "component": component,
                        "expectancy_usd": round(exp, 4),
                        "marker": _MARKER_DENY,
                    }
                )

    candidates.sort(key=lambda x: float(x.get("expectancy_usd") or 0), reverse=True)
    out = {
        "ok": True,
        "session_date_et": today,
        "skim_denylist_count": len(skim_deny),
        "infra_denylist_count": len(infra_deny),
        "skim_sources": {k: v for k, v in skim_sources.items() if k != "env_key"},
        "infra_sources": {k: v for k, v in infra_sources.items() if k != "env_key"},
        "skim_decision_blocked": skim_decision,
        "infra_decision_blocked": infra_decision,
        "skim_pause_entries": skim_pause_learned,
        "infra_pause_entries": infra_pause_learned,
        "skim_blocked_in_universe": skim_overlap,
        "infra_blocked_in_universe": infra_overlap,
        "thaw_candidates": candidates[:12],
        "marker": _MARKER_DENY,
        "ts": now_iso(),
    }
    log.info(
        "%s skim_blocked=%s infra_blocked=%s decision_skim=%s candidates=%s",
        _MARKER_DENY,
        skim_overlap[:8],
        infra_overlap[:8],
        skim_decision[:8],
        [c["symbol"] for c in candidates[:6]],
    )
    return out


def apply_deep_lag_wait_strategy(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """Deep alpha lag on strong tape: wait — do not force participation via override."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_DEEP}
    ensure_participation_policy_session()
    port = port if port is not None else _portfolio()
    alpha = port.get("alpha_vs_spy_pct")
    strong = bool(port.get("strong_tape_1d"))
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    if not strong or alpha_f is None or alpha_f > floor:
        return {
            "skipped": "not_deep_lag",
            "alpha_vs_spy_pct": alpha_f,
            "deep_floor": floor,
            "strong_tape_1d": strong,
            "marker": _MARKER_DEEP,
        }

    audit = audit_denylist_vs_universe()
    today = now().date().isoformat()
    policy = _load_policy()
    if str(policy.get("session_date_et") or "") != today:
        policy = {"session_date_et": today, "events": []}
    # Once per session scoreable deep_lag event
    already = any(
        isinstance(ev, dict) and str(ev.get("strategy")) == "deep_lag_wait"
        for ev in (policy.get("events") or [])
    )
    policy.update(
        {
            "strategy": "deep_lag_wait",
            "alpha_vs_spy_pct": alpha_f,
            "deep_floor": floor,
            "strong_tape_1d": True,
            "participation_shortfall_exits": port.get("participation_shortfall_exits"),
            "thaw_candidates_pending_alpha_recovery": audit.get("thaw_candidates") or [],
            "marker": _MARKER_DEEP,
            "updated_utc": now_iso(),
        }
    )
    events = list(policy.get("events") or [])
    events.append(
        {
            "ts": now_iso(),
            "strategy": "deep_lag_wait",
            "alpha": alpha_f,
            "marker": _MARKER_DEEP,
        }
    )
    policy["events"] = events[-20:]
    _save_policy(policy)

    session_notes: dict[str, Any] = {}
    for component in ("skim_swarm", "infra_swarm"):
        try:
            from utils.swarm_session_si import load_session_policy, save_session_policy

            pol = load_session_policy(component)
            boost = float(pol.get("enter_long_delta_boost") or 0)
            pol["enter_long_delta_boost"] = round(min(0.06, max(boost, 0.02)), 4)
            pol["si_deep_lag_wait"] = True
            pol["notes"] = list(pol.get("notes") or [])[-6:] + [
                f"{_MARKER_DEEP} alpha={alpha_f:.3f}<{floor}"
            ]
            save_session_policy(component, pol)
            session_notes[component] = {"enter_long_delta_boost": pol["enter_long_delta_boost"]}
        except Exception as e:
            session_notes[component] = {"error": str(e)[:80]}

    if not already:
        try:
            from utils.si_capability_review import collect_metrics
            from utils.si_intervention_log import record_intervention

            record_intervention(
                component="portfolio_session",
                action="deep_lag_wait",
                metrics_snapshot=collect_metrics(),
                detail={
                    "marker": _MARKER_DEEP,
                    "alpha_vs_spy_pct": alpha_f,
                    "deep_floor": floor,
                    "audit": {
                        "skim_blocked": audit.get("skim_blocked_in_universe"),
                        "infra_blocked": audit.get("infra_blocked_in_universe"),
                        "decision_blocked": audit.get("skim_decision_blocked"),
                        "candidates": audit.get("thaw_candidates"),
                    },
                    "session_notes": session_notes,
                },
                scoreable=True,
            )
        except Exception:
            pass

    return {
        "ok": True,
        "strategy": "deep_lag_wait",
        "alpha_vs_spy_pct": alpha_f,
        "deep_floor": floor,
        "audit": audit,
        "session_notes": session_notes,
        "scoreable_once": not already,
        "marker": _MARKER_DEEP,
    }


def maybe_thaw_denylist_on_recovery(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """When alpha recovers above soft threshold, thaw top positive-expectancy denylist names."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_THAW}
    if str(os.environ.get("FORTRESS_SI_DENYLIST_THAW", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "thaw_disabled", "marker": _MARKER_THAW}

    port = port if port is not None else _portfolio()
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    soft = _soft_alpha()
    if alpha_f is None or alpha_f < soft:
        return {
            "skipped": "alpha_not_recovered",
            "alpha_vs_spy_pct": alpha_f,
            "soft_threshold": soft,
            "marker": _MARKER_THAW,
        }

    try:
        max_thaw = max(0, int(os.environ.get("FORTRESS_SI_DENYLIST_THAW_MAX", "2") or 2))
    except ValueError:
        max_thaw = 2

    audit = audit_denylist_vs_universe()
    candidates = list(audit.get("thaw_candidates") or [])[:max_thaw]
    if not candidates:
        return {"ok": True, "thawed": [], "marker": _MARKER_THAW, "note": "no_candidates"}

    thawed: list[str] = []
    for c in candidates:
        sym = str(c.get("symbol") or "")
        component = str(c.get("component") or "skim_swarm")
        if not sym:
            continue
        try:
            if component == "infra_swarm":
                from utils import infra_swarm_config as cfg
            else:
                from utils import skim_swarm_config as cfg
            path = cfg._swarm_data_dir_path() / "runtime_overrides.json"
            if not path.is_file():
                continue
            ov = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(ov, dict):
                continue
            raw = list(ov.get("denylist_symbols") or [])
            new_list = [
                x
                for x in raw
                if cfg.normalize_symbol(str(x)) != cfg.normalize_symbol(sym)
            ]
            if len(new_list) == len(raw):
                review = ov.get("review_actions") if isinstance(ov.get("review_actions"), dict) else {}
                pause = list(review.get("pause_symbols") or ov.get("pause_symbols") or [])
                new_pause = [
                    x
                    for x in pause
                    if cfg.normalize_symbol(str(x)) != cfg.normalize_symbol(sym)
                ]
                if len(new_pause) == len(pause):
                    continue
                if "review_actions" in ov and isinstance(ov["review_actions"], dict):
                    ov["review_actions"]["pause_symbols"] = new_pause
                else:
                    ov["pause_symbols"] = new_pause
            else:
                ov["denylist_symbols"] = new_list
            ov["si_denylist_thaw"] = {
                "ts": now_iso(),
                "symbol": sym,
                "marker": _MARKER_THAW,
                "alpha_vs_spy_pct": alpha_f,
            }
            path.write_text(json.dumps(ov, indent=2), encoding="utf-8")
            thawed.append(f"{component}:{sym}")
        except Exception as e:
            log.warning("denylist_thaw_failed %s: %s", sym, e)

    if thawed:
        try:
            from utils.si_capability_review import collect_metrics
            from utils.si_intervention_log import record_intervention

            record_intervention(
                component="portfolio_session",
                action="denylist_thaw",
                metrics_snapshot=collect_metrics(),
                detail={"marker": _MARKER_THAW, "thawed": thawed, "alpha_vs_spy_pct": alpha_f},
                scoreable=True,
            )
        except Exception:
            pass

    return {"ok": True, "thawed": thawed, "alpha_vs_spy_pct": alpha_f, "marker": _MARKER_THAW}


def _infra_near_entry_threshold() -> dict[str, Any]:
    """True when recent infra decisions show entry scores near the soft-path window."""
    try:
        floor = float(os.environ.get("FORTRESS_SI_INFRA_SOFT_SCORE_FLOOR", "-0.05") or -0.05)
    except (TypeError, ValueError):
        floor = -0.05
    path = _data_dir() / "infra_swarm" / "decisions.jsonl"
    scores: list[float] = []
    if path.is_file():
        try:
            raw = path.read_bytes()[-250_000:]
            today = now().date().isoformat()
            for line in raw.decode("utf-8", errors="replace").splitlines()[::-1]:
                if len(scores) >= 40:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = str(row.get("ts") or "")
                if today not in ts and str(row.get("session_date_et") or "") != today:
                    continue
                blob = json.dumps(row, default=str)
                for m in _SCORE_RE.finditer(blob):
                    try:
                        scores.append(float(m.group(1)))
                    except ValueError:
                        pass
        except Exception:
            pass
    if not scores:
        # No measured near-miss — do not apply soft path (avoids dead-weight boost spam).
        return {"near": False, "reason": "no_recent_scores", "max_score": None, "floor": floor}
    mx = max(scores)
    return {
        "near": mx >= floor,
        "max_score": round(mx, 4),
        "n_scores": len(scores),
        "floor": floor,
        "reason": "ok" if mx >= floor else "scores_too_weak",
    }


def apply_infra_strong_tape_soft_path(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """Strong-tape day, 0 exits, not deep lag: ease infra once/session if scores near enter."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_INFRA}
    if str(os.environ.get("FORTRESS_SI_INFRA_STRONG_TAPE_SOFT", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "soft_path_disabled", "marker": _MARKER_INFRA}

    ensure_participation_policy_session()
    port = port if port is not None else _portfolio()
    strong = bool(port.get("strong_tape_1d"))
    exits = int(port.get("session_exit_count") or 0)
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    if not strong or exits > 0:
        return {
            "skipped": "not_idle_strong_tape",
            "strong_tape_1d": strong,
            "session_exit_count": exits,
            "marker": _MARKER_INFRA,
        }
    if alpha_f is not None and alpha_f <= floor:
        return {
            "skipped": "deep_lag_blocks_soft_path",
            "alpha_vs_spy_pct": alpha_f,
            "marker": _MARKER_INFRA,
        }

    near = _infra_near_entry_threshold()
    if not near.get("near"):
        return {
            "skipped": "not_near_entry_threshold",
            "near_check": near,
            "marker": _MARKER_INFRA,
        }

    try:
        ease = float(os.environ.get("FORTRESS_SI_INFRA_SOFT_ENTER_DELTA", "-0.025") or -0.025)
    except (TypeError, ValueError):
        ease = -0.025
    ease = max(-0.05, min(0.0, ease))
    try:
        from utils.si_predictability import prediction_scale_multipliers

        soft_m = float(prediction_scale_multipliers().get("soft_path_mult") or 1.0)
        # soft_m < 1 shrinks ease (less aggressive); > 1 deepens slightly toward -0.05 cap.
        ease = max(-0.05, min(0.0, ease * soft_m))
    except Exception:
        pass

    from utils.swarm_session_si import load_session_policy, save_session_policy

    today = now().date().isoformat()
    pol = load_session_policy("infra_swarm")
    if pol.get("si_deep_lag_wait"):
        return {"skipped": "deep_lag_wait_active", "marker": _MARKER_INFRA}
    if str(pol.get("si_infra_strong_tape_soft_session") or "") == today:
        return {
            "skipped": "already_applied_session",
            "enter_long_delta_boost": pol.get("enter_long_delta_boost"),
            "marker": _MARKER_INFRA,
        }

    prev = float(pol.get("enter_long_delta_boost") or 0)
    new_boost = round(min(prev, ease), 4)
    pol["enter_long_delta_boost"] = new_boost
    pol["enter_short_delta_boost"] = round(max(float(pol.get("enter_short_delta_boost") or 0), 0.0), 4)
    pol["si_infra_strong_tape_soft"] = True
    pol["si_infra_strong_tape_soft_session"] = today
    notes = list(pol.get("notes") or [])
    notes.append(f"{_MARKER_INFRA} enter_long_delta_boost={new_boost} max_score={near.get('max_score')}")
    pol["notes"] = notes[-8:]
    save_session_policy("infra_swarm", pol)

    pdoc = _load_policy()
    events = list(pdoc.get("events") or [])
    events.append(
        {
            "ts": now_iso(),
            "strategy": "infra_strong_tape_soft",
            "enter_long_delta_boost": new_boost,
            "near": near,
            "marker": _MARKER_INFRA,
        }
    )
    pdoc["events"] = events[-20:]
    pdoc["infra_soft_session"] = today
    pdoc["updated_utc"] = now_iso()
    _save_policy(pdoc)

    try:
        from utils.si_capability_review import collect_metrics
        from utils.si_intervention_log import record_intervention

        record_intervention(
            component="infra_swarm",
            action="infra_strong_tape_soft",
            metrics_snapshot=collect_metrics(),
            detail={
                "marker": _MARKER_INFRA,
                "enter_long_delta_boost": new_boost,
                "alpha_vs_spy_pct": alpha_f,
                "near_check": near,
                "once_per_session": True,
            },
            scoreable=True,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "enter_long_delta_boost": new_boost,
        "alpha_vs_spy_pct": alpha_f,
        "near_check": near,
        "marker": _MARKER_INFRA,
    }


def run_participation_si_cycle(*, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Orchestrate deep-lag / denylist thaw / infra soft path for RTH SI."""
    if not _enabled():
        return {"skipped": "participation_policy_disabled"}
    rollover = ensure_participation_policy_session()
    port = (metrics or {}).get("portfolio_session") if metrics else None
    if not isinstance(port, dict) or not port:
        port = _portfolio()

    out: dict[str, Any] = {
        "ok": True,
        "marker": "si_participation_cycle",
        "session_policy": {
            "session_date_et": rollover.get("session_date_et"),
            "rollover_from": rollover.get("rollover_from"),
        },
    }
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    soft = _soft_alpha()

    if bool(port.get("strong_tape_1d")) and alpha_f is not None and alpha_f <= floor:
        out["deep_lag"] = apply_deep_lag_wait_strategy(port=port)
        out["denylist_audit"] = out["deep_lag"].get("audit") if isinstance(out["deep_lag"], dict) else None
    else:
        out["deep_lag"] = {"skipped": "not_deep_lag"}
        if bool(port.get("strong_tape_1d")):
            out["denylist_audit"] = audit_denylist_vs_universe()
        if alpha_f is not None and alpha_f >= soft:
            out["thaw"] = maybe_thaw_denylist_on_recovery(port=port)
        out["infra_soft"] = apply_infra_strong_tape_soft_path(port=port)

    return out


__all__ = [
    "apply_deep_lag_wait_strategy",
    "apply_infra_strong_tape_soft_path",
    "audit_denylist_vs_universe",
    "ensure_participation_policy_session",
    "maybe_thaw_denylist_on_recovery",
    "run_participation_si_cycle",
]
