"""SI participation policy — true SI acts on strong-tape / lag situations.

Strategies (never loosens pre_trade_gate or MR rails):
1. Deep lag (alpha << deep floor on strong tape): wait + denylist audit; no tape override.
2. Mid lag (strong tape, shortfall, alpha soft but not deep): denylist audit + slight tighten; no soft.
3. Alpha recovered: selective denylist thaw of high-rolling-expectancy universe names.
4. Strong tape, 0 exits, not mid/deep-lagging, near-threshold scores: infra soft entry (once/session).
   Soft is blocked when market_relative blocks dominate session decision trails.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from utils.system_time import now, now_iso

log = logging.getLogger(__name__)

_MARKER_DEEP = "si_deep_lag_wait"
_MARKER_MID = "si_mid_lag_participation"
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
    from utils.si_decision_scan import block_reason, item_symbol, iter_session_decisions

    sub = "skim_swarm" if "infra" not in component else "infra_swarm"
    if component in ("skim_swarm", "infra_swarm"):
        sub = component
    path = _data_dir() / sub / "decisions.jsonl"
    found: set[str] = set()
    for _wave, item in iter_session_decisions(path, today=today):
        br = block_reason(item)
        if br != "manual_denylist" and "denylist" not in br.lower():
            continue
        sym = item_symbol(item)
        if sym:
            found.add(sym)
    return sorted(found)


def _persist_observed_denylist(component: str, symbols: list[str], today: str) -> None:
    """Mirror decision-trail denylist so SI/RTH (missing env vars) still sees effective set."""
    if not symbols:
        return
    path = _data_dir() / "si_capability" / "observed_denylist.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc[component] = {
        "session_date_et": today,
        "symbols": sorted(set(symbols)),
        "ts": now_iso(),
        "marker": _MARKER_DENY,
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _observed_denylist_symbols(component: str, *, today: str) -> list[str]:
    path = _data_dir() / "si_capability" / "observed_denylist.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(doc, dict):
        return []
    slot = doc.get(component) if isinstance(doc.get(component), dict) else {}
    if str(slot.get("session_date_et") or "") != today:
        return []
    return [str(x).upper() for x in (slot.get("symbols") or []) if str(x).strip()]


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
    # Persist so later SI processes (RTH without skim env) see truth.
    _persist_observed_denylist("skim_swarm", skim_decision, today)
    _persist_observed_denylist("infra_swarm", infra_decision, today)
    skim_observed = _observed_denylist_symbols("skim_swarm", today=today)
    infra_observed = _observed_denylist_symbols("infra_swarm", today=today)
    skim_pause_learned = _session_pause_entry_symbols("skim_swarm", today=today)
    infra_pause_learned = _session_pause_entry_symbols("infra_swarm", today=today)

    # Union that matches signal gate + observed blocks + session brakes.
    skim_effective = skim_deny | set(skim_decision) | set(skim_observed)
    infra_effective = infra_deny | set(infra_decision) | set(infra_observed)

    skim_overlap = sorted(skim_effective & skim_uni)
    infra_overlap = sorted(infra_effective & infra_uni)

    env_empty_but_blocking = bool(skim_decision) and not skim_deny and not skim_sources.get("env")
    source_mismatch = {
        "skim": env_empty_but_blocking,
        "note": (
            "process env denylist not visible to this SI process; "
            "using decision-trail + observed_denylist.json"
            if env_empty_but_blocking
            else None
        ),
    }

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
        "skim_denylist_count": len(skim_effective),
        "infra_denylist_count": len(infra_effective),
        "skim_sources": {k: v for k, v in skim_sources.items() if k != "env_key"},
        "infra_sources": {k: v for k, v in infra_sources.items() if k != "env_key"},
        "skim_decision_blocked": skim_decision,
        "infra_decision_blocked": infra_decision,
        "skim_observed": skim_observed,
        "infra_observed": infra_observed,
        "source_mismatch": source_mismatch,
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
    from utils.si_decision_scan import block_reason, iter_session_decisions

    try:
        floor = float(os.environ.get("FORTRESS_SI_INFRA_SOFT_SCORE_FLOOR", "-0.05") or -0.05)
    except (TypeError, ValueError):
        floor = -0.05
    path = _data_dir() / "infra_swarm" / "decisions.jsonl"
    scores: list[float] = []
    today = now().date().isoformat()
    for _wave, item in iter_session_decisions(path, today=today):
        if len(scores) >= 40:
            break
        blob = json.dumps(item, default=str)
        for m in _SCORE_RE.finditer(blob):
            try:
                scores.append(float(m.group(1)))
            except ValueError:
                pass
        # Also raw decision.score near-miss
        dec = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        sc = dec.get("score")
        if sc is not None:
            try:
                scores.append(float(sc))
            except (TypeError, ValueError):
                pass
        br = block_reason(item)
        # keep focus on no_entry-ish reasons for threshold scores
        if "no_entry" not in br and sc is None:
            pass
    if not scores:
        return {"near": False, "reason": "no_recent_scores", "max_score": None, "floor": floor}
    mx = max(scores)
    return {
        "near": mx >= floor,
        "max_score": round(mx, 4),
        "n_scores": len(scores),
        "floor": floor,
        "reason": "ok" if mx >= floor else "scores_too_weak",
    }


def _infra_session_exits() -> int:
    try:
        from utils.swarm_session_si import load_session_policy

        pol = load_session_policy("infra_swarm")
        return int(pol.get("session_exits") or 0)
    except Exception:
        return 0


def _infra_rolling_positive() -> bool:
    try:
        from utils.si_capability_review import collect_metrics

        m = (collect_metrics() or {}).get("infra_swarm") or {}
        exp = m.get("rolling_expectancy_usd")
        return exp is not None and float(exp) > 0
    except Exception:
        return False


def _session_block_counts(component: str) -> Counter[str]:
    from utils.si_decision_scan import block_reason, iter_session_decisions

    sub = "infra_swarm" if "infra" in component else "skim_swarm"
    if component in ("skim_swarm", "infra_swarm"):
        sub = component
    path = _data_dir() / sub / "decisions.jsonl"
    today = now().date().isoformat()
    ctr: Counter[str] = Counter()
    for _wave, item in iter_session_decisions(path, today=today):
        br = block_reason(item)
        if not br:
            continue
        key = br.split(":")[0].strip()
        if key:
            ctr[key] += 1
    return ctr


def _mr_blocks_dominate(component: str = "infra_swarm") -> dict[str, Any]:
    """True when market-relative underperformance is a large share of session blocks."""
    try:
        ratio_thr = float(os.environ.get("FORTRESS_SI_SOFT_MR_BLOCK_RATIO", "0.35") or 0.35)
    except (TypeError, ValueError):
        ratio_thr = 0.35
    try:
        min_mr = max(2, int(os.environ.get("FORTRESS_SI_SOFT_MR_BLOCK_MIN", "3") or 3))
    except ValueError:
        min_mr = 3
    ctr = _session_block_counts(component)
    total = int(sum(ctr.values()))
    mr = 0
    for k, v in ctr.items():
        kl = k.lower()
        if "market_relative" in kl or kl.startswith("mr_") or "underperformance" in kl:
            mr += int(v)
    ratio = (mr / total) if total else 0.0
    dominate = mr >= min_mr and ratio >= ratio_thr
    return {
        "dominate": dominate,
        "mr_blocks": mr,
        "total_blocks": total,
        "ratio": round(ratio, 4),
        "ratio_threshold": ratio_thr,
        "min_mr": min_mr,
        "top": dict(ctr.most_common(6)),
    }


def _mid_alpha_ceiling() -> float:
    """Alpha must be below this (more lagging) to count as mid-lag; above deep floor."""
    try:
        return float(os.environ.get("FORTRESS_SI_MID_LAG_ALPHA_CEIL", "-0.25") or -0.25)
    except (TypeError, ValueError):
        return -0.25


def _mid_shortfall_min() -> int:
    try:
        return max(2, int(os.environ.get("FORTRESS_SI_MID_LAG_SHORTFALL_MIN", "3") or 3))
    except ValueError:
        return 3


def apply_mid_lag_strategy(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """Strong tape + participation shortfall + alpha lagging (not deep): focus, don't force soft.

    Actions:
    - denylist audit (surface blocked universe names)
    - slight enter tighten (prefer quality over size); never disable MR
    - once per session scoreable intervention
    """
    if not _enabled():
        return {"skipped": "participation_policy_disabled", "marker": _MARKER_MID}
    if str(os.environ.get("FORTRESS_SI_MID_LAG", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "mid_lag_disabled", "marker": _MARKER_MID}

    ensure_participation_policy_session()
    port = port if port is not None else _portfolio()
    strong = bool(port.get("strong_tape_1d"))
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    try:
        shortfall = int(port.get("participation_shortfall_exits") or 0)
    except (TypeError, ValueError):
        shortfall = 0
    deep = _deep_floor()
    ceil = _mid_alpha_ceiling()
    need_sf = _mid_shortfall_min()

    if not strong:
        return {"skipped": "not_strong_tape", "marker": _MARKER_MID}
    if alpha_f is None:
        return {"skipped": "alpha_unknown", "marker": _MARKER_MID}
    if alpha_f <= deep:
        return {
            "skipped": "deep_lag_prefers_wait",
            "alpha_vs_spy_pct": alpha_f,
            "deep_floor": deep,
            "marker": _MARKER_MID,
        }
    if alpha_f > ceil:
        return {
            "skipped": "alpha_not_mid_lag",
            "alpha_vs_spy_pct": alpha_f,
            "mid_alpha_ceil": ceil,
            "marker": _MARKER_MID,
        }
    if shortfall < need_sf:
        return {
            "skipped": "shortfall_low",
            "participation_shortfall_exits": shortfall,
            "need": need_sf,
            "marker": _MARKER_MID,
        }

    today = now().date().isoformat()
    policy = _load_policy()
    if str(policy.get("mid_lag_session") or "") == today or any(
        isinstance(ev, dict) and str(ev.get("strategy")) == "mid_lag_focus" for ev in (policy.get("events") or [])
    ):
        return {
            "skipped": "already_applied_session",
            "strategy": "mid_lag_focus",
            "marker": _MARKER_MID,
        }

    audit = audit_denylist_vs_universe()
    try:
        tighten = float(os.environ.get("FORTRESS_SI_MID_LAG_ENTER_DELTA", "0.02") or 0.02)
    except (TypeError, ValueError):
        tighten = 0.02
    tighten = max(0.0, min(0.06, tighten))

    session_notes: dict[str, Any] = {}
    for component in ("skim_swarm", "infra_swarm"):
        try:
            from utils.swarm_session_si import load_session_policy, save_session_policy

            pol = load_session_policy(component)
            boost = float(pol.get("enter_long_delta_boost") or 0)
            # Mid-lag prefers slightly harder entries (positive boost = tighter long entry),
            # never eases. Clamp under soft-path negatives by lifting toward tighten.
            new_boost = round(max(boost, tighten), 4)
            pol["enter_long_delta_boost"] = new_boost
            pol["si_mid_lag_focus"] = True
            pol["si_mid_lag_session"] = today
            notes = list(pol.get("notes") or [])
            notes.append(
                f"{_MARKER_MID} shortfall={shortfall} alpha={alpha_f:.3f} "
                f"blocked={len((audit.get('skim_blocked_in_universe') or []) if component == 'skim_swarm' else (audit.get('infra_blocked_in_universe') or []))}"
            )
            pol["notes"] = notes[-8:]
            save_session_policy(component, pol)
            session_notes[component] = {"enter_long_delta_boost": new_boost}
        except Exception as e:
            session_notes[component] = {"error": str(e)[:80]}

    events = list(policy.get("events") or [])
    events.append(
        {
            "ts": now_iso(),
            "strategy": "mid_lag_focus",
            "alpha": alpha_f,
            "shortfall": shortfall,
            "marker": _MARKER_MID,
        }
    )
    policy.update(
        {
            "session_date_et": today,
            "strategy": "mid_lag_focus",
            "mid_lag_session": today,
            "alpha_vs_spy_pct": alpha_f,
            "participation_shortfall_exits": shortfall,
            "marker": _MARKER_MID,
            "updated_utc": now_iso(),
            "events": events[-20:],
            "thaw_candidates_pending_alpha_recovery": audit.get("thaw_candidates") or [],
        }
    )
    _save_policy(policy)

    try:
        from utils.si_capability_review import collect_metrics
        from utils.si_intervention_log import record_intervention

        record_intervention(
            component="portfolio_session",
            action="mid_lag_focus",
            metrics_snapshot=collect_metrics(),
            detail={
                "marker": _MARKER_MID,
                "alpha_vs_spy_pct": alpha_f,
                "shortfall": shortfall,
                "audit": {
                    "skim_blocked": audit.get("skim_blocked_in_universe"),
                    "infra_blocked": audit.get("infra_blocked_in_universe"),
                    "decision_blocked": audit.get("skim_decision_blocked"),
                },
                "session_notes": session_notes,
                "do_not_disable_mr": True,
            },
            scoreable=True,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "strategy": "mid_lag_focus",
        "alpha_vs_spy_pct": alpha_f,
        "participation_shortfall_exits": shortfall,
        "audit": audit,
        "session_notes": session_notes,
        "marker": _MARKER_MID,
    }


def apply_infra_strong_tape_soft_path(*, port: dict[str, Any] | None = None) -> dict[str, Any]:
    """Idle infra + constructive setup: ease enter once/session if scores near threshold.

    Conditions (never deep lag, never mid-lag, never when SI deep-lag wait active):
    - strong tape OR positive rolling infra expectancy
    - infra sleeve has 0 session exits (not portfolio-wide exits)
    - recent scores near enter floor (score-family gated)
    - market_relative blocks do not dominate the session trail
    """
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
    today = now().date().isoformat()
    # Hard once-per-session on participation policy first (survives adapt rewrite).
    pdoc_early = _load_policy()
    if str(pdoc_early.get("infra_soft_session") or "") == today:
        return {
            "skipped": "already_applied_session",
            "source": "participation_policy",
            "marker": _MARKER_INFRA,
        }
    if str(pdoc_early.get("mid_lag_session") or "") == today or str(pdoc_early.get("strategy") or "") in (
        "mid_lag_focus",
        "deep_lag_wait",
    ):
        return {
            "skipped": "mid_or_deep_lag_blocks_soft",
            "strategy": pdoc_early.get("strategy"),
            "marker": _MARKER_INFRA,
        }

    port = port if port is not None else _portfolio()
    strong = bool(port.get("strong_tape_1d"))
    infra_exits = _infra_session_exits()
    roll_pos = _infra_rolling_positive()
    alpha = port.get("alpha_vs_spy_pct")
    try:
        alpha_f = float(alpha) if alpha is not None else None
    except (TypeError, ValueError):
        alpha_f = None
    floor = _deep_floor()
    eligible = (strong or roll_pos) and infra_exits == 0
    if not eligible:
        return {
            "skipped": "not_idle_infra_soft_setup",
            "strong_tape_1d": strong,
            "infra_session_exits": infra_exits,
            "rolling_positive": roll_pos,
            "marker": _MARKER_INFRA,
        }
    if alpha_f is not None and alpha_f <= floor:
        return {
            "skipped": "deep_lag_blocks_soft_path",
            "alpha_vs_spy_pct": alpha_f,
            "marker": _MARKER_INFRA,
        }

    mr = _mr_blocks_dominate("infra_swarm")
    if mr.get("dominate"):
        return {
            "skipped": "mr_blocks_dominate",
            "mr_check": mr,
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

    pol = load_session_policy("infra_swarm")
    if pol.get("si_deep_lag_wait") or pol.get("si_mid_lag_focus"):
        return {
            "skipped": "deep_or_mid_lag_wait_active",
            "marker": _MARKER_INFRA,
        }
    if str(pol.get("si_infra_strong_tape_soft_session") or "") == today:
        return {
            "skipped": "already_applied_session",
            "source": "session_policy",
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
    pdoc["session_date_et"] = today
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
                "mr_check": mr,
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
        "mr_check": mr,
        "marker": _MARKER_INFRA,
    }


def run_participation_si_cycle(*, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Orchestrate deep / mid lag, denylist thaw, and gated infra soft path for RTH SI."""
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
        out["mid_lag"] = {"skipped": "deep_lag_active"}
        out["infra_soft"] = {"skipped": "deep_lag_blocks_soft_path"}
    else:
        out["deep_lag"] = {"skipped": "not_deep_lag"}
        # Always reconcile denylist truth vs decision trail (env may be invisible to RTH).
        out["denylist_audit"] = audit_denylist_vs_universe()
        out["mid_lag"] = apply_mid_lag_strategy(port=port)
        if alpha_f is not None and alpha_f >= soft:
            out["thaw"] = maybe_thaw_denylist_on_recovery(port=port)
        # Soft path only when not mid-lag focus (handler also double-checks).
        mid_ok = isinstance(out.get("mid_lag"), dict) and out["mid_lag"].get("ok")
        if mid_ok:
            out["infra_soft"] = {"skipped": "mid_lag_focus_active", "marker": _MARKER_INFRA}
        else:
            out["infra_soft"] = apply_infra_strong_tape_soft_path(port=port)

    return out


__all__ = [
    "apply_deep_lag_wait_strategy",
    "apply_infra_strong_tape_soft_path",
    "apply_mid_lag_strategy",
    "audit_denylist_vs_universe",
    "ensure_participation_policy_session",
    "maybe_thaw_denylist_on_recovery",
    "run_participation_si_cycle",
]
