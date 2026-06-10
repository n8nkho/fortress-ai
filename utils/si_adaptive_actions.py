"""
Adaptive SI actions — scale interventions from measured gaps (session + rolling).

Never weakens pre-trade gate or immutable caps; all knobs bounded via si_capability_registry.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    root = Path(__file__).resolve().parent.parent
    return Path(raw) if raw else (root / "data")


def _enabled() -> bool:
    return str(os.environ.get("FORTRESS_SI_ADAPTIVE_ACTIONS", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _get_cap(name: str, default: float) -> float:
    try:
        from utils.si_capability_review import get_capability

        return float(get_capability(name, default) or default)
    except Exception:
        return default


def _clamp_cap(name: str, value: float) -> float:
    from utils.si_capability_review import _clamp_capability

    return _clamp_capability(name, value)


def rolling_metrics(component: str) -> dict[str, Any]:
    try:
        from utils.si_capability_review import _component_session_metrics

        return _component_session_metrics(component, window_sessions=5)
    except Exception:
        return {}


def adaptive_strength_from_gaps(gaps: list[dict[str, Any]], *, component: str) -> float:
    """0..1 severity from open capability gaps for a component."""
    base = float(_get_cap("rolling_edge_autofix_strength", 0.55))
    sev = 0.0
    for g in gaps:
        if str(g.get("component") or "") != component:
            continue
        gap = float(g.get("gap") or 0)
        pri = str(g.get("priority") or "medium")
        weight = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.2}.get(pri, 0.4)
        sev = max(sev, min(1.0, gap * weight * 2.0))
    return round(min(1.0, base * max(0.15, sev)), 4)


def apply_rolling_aware_edge_autofix(
    component: str,
    scorecard: dict[str, Any],
    *,
    gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    When session scorecard looks OK but rolling metrics / capability gaps say otherwise,
    apply scaled-down edge autofix (adaptive strength).
    """
    if not _enabled():
        return {"skipped": "adaptive_disabled"}
    gaps = gaps or []
    rolling = rolling_metrics(component)
    roll_pay = rolling.get("rolling_payoff_ratio")
    roll_exp = rolling.get("rolling_expectancy_usd")
    strength = adaptive_strength_from_gaps(gaps, component=component)
    if roll_pay is not None and float(roll_pay) < 1.0:
        strength = max(strength, min(1.0, (1.0 - float(roll_pay)) * 0.6))
    if roll_exp is not None and float(roll_exp) < 0:
        strength = max(strength, min(1.0, abs(float(roll_exp)) * 3.0))

    pay = scorecard.get("payoff_ratio")
    if pay is None or strength < 0.08:
        return {"skipped": "no_rolling_pressure", "strength": strength}

    pay_f = float(pay)
    if pay_f < 0.95:
        return {"skipped": "session_autofix_handles", "strength": strength}

    from utils.edge_autofix import load_runtime_overrides, save_runtime_overrides
    from utils.swarm_session_si import load_session_policy, save_session_policy

    pol = load_session_policy(component)
    ov = load_runtime_overrides(component)
    changes: list[str] = []

    delta_step = round(0.015 * strength, 4)
    pol["enter_long_delta_boost"] = round(
        min(0.12, float(pol.get("enter_long_delta_boost") or 0) + delta_step),
        4,
    )
    pol["enter_short_delta_boost"] = round(
        max(-0.12, float(pol.get("enter_short_delta_boost") or 0) - delta_step),
        4,
    )
    changes.append(f"rolling_aware_delta L={pol['enter_long_delta_boost']}")

    try:
        from utils.si_capability_review import effective_edge_autofix_rr_boost_cap

        rr_cap = effective_edge_autofix_rr_boost_cap()
    except Exception:
        rr_cap = 0.12
    boost = min(rr_cap, float(ov.get("rr_safety_margin_session_boost") or 0) + 0.02 * strength)
    ov["rr_safety_margin_session_boost"] = round(boost, 4)
    changes.append(f"rolling_rr_boost={boost:.3f}")

    if roll_exp is not None and float(roll_exp) < -0.05 and strength >= 0.35:
        mult = min(1.35, float(pol.get("cycle_interval_mult") or 1.0) + 0.03 * strength)
        pol["cycle_interval_mult"] = round(mult, 3)
        changes.append(f"rolling_slow_cycle={mult}")

    if strength >= 0.2:
        pol["mode"] = "rolling_aware"
        if roll_exp is not None and float(roll_exp) < 0:
            pol["rolling_negative_edge"] = True

    notes = list(pol.get("notes") or [])
    notes.append(
        f"rolling_edge_autofix:strength={strength} roll_pay={roll_pay} roll_exp={roll_exp}"
    )
    pol["notes"] = notes[-12:]
    pol["edge_autofix_ts"] = datetime.now(timezone.utc).isoformat()
    save_session_policy(component, pol)
    ov["updated_utc"] = datetime.now(timezone.utc).isoformat()
    ov["last_rolling_edge_autofix"] = {"strength": strength, "ts": ov["updated_utc"]}
    save_runtime_overrides(component, ov)
    return {"component": component, "changes": changes, "strength": strength, "mode": "rolling_aware"}


def _swarm_learned_dir(component: str) -> Path:
    name = component if component.endswith("_swarm") else f"{component}_swarm"
    return _data_dir() / name / "learned"


def _session_date(component: str) -> str:
    if component == "skim_swarm":
        from agents.skim_swarm.eod import session_date_et

        return session_date_et()
    from agents.infra_swarm.eod import session_date_et

    return session_date_et()


def _save_learned(component: str, symbol: str, doc: dict[str, Any]) -> None:
    sym = symbol.upper().replace(".", "_")
    path = _swarm_learned_dir(component) / f"{sym.lower()}.json"
    if not path.exists():
        alt = _swarm_learned_dir(component) / f"{sym}.json"
        if alt.exists():
            path = alt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _load_learned(component: str, symbol: str) -> dict[str, Any]:
    if component == "skim_swarm":
        from agents.skim_swarm.symbol_learning import load_learned

        return load_learned(symbol)
    from agents.infra_swarm.symbol_learning import load_learned

    return load_learned(symbol)


def apply_symbol_session_brakes(component: str) -> dict[str, Any]:
    """
    Per-symbol adaptive loss brake — independent of swarm-wide session SI.
    Scales threshold from session PnL dispersion and capability knob.
    """
    if not _enabled():
        return {"skipped": "adaptive_disabled", "brakes": []}
    learned_dir = _swarm_learned_dir(component)
    if not learned_dir.is_dir():
        return {"brakes": []}

    sess = _session_date(component)
    losses: list[tuple[float, int, str, dict[str, Any]]] = []
    for f in learned_dir.glob("*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if doc.get("session_date_et") != sess:
            continue
        st = doc.get("session_stats") or {}
        ex = int(st.get("exits") or 0)
        pnl = float(st.get("sum_pnl_usd") or 0)
        if ex > 0:
            losses.append((pnl, ex, f.stem.upper(), doc))

    if not losses:
        return {"brakes": []}

    winners = [x for x in losses if x[0] > 0]
    avg_win = sum(x[0] for x in winners) / len(winners) if winners else 0.25
    brake_mult = float(_get_cap("symbol_session_loss_brake_mult", 1.0))
    min_loss = max(0.12, avg_win * 0.35 * brake_mult)
    min_exits = max(2, min(5, int(2 + brake_mult)))

    brakes: list[str] = []
    for pnl, ex, sym, doc in losses:
        if pnl >= -min_loss or ex < min_exits:
            continue
        severity = min(1.0, abs(pnl) / max(min_loss, 0.01))
        params = doc.setdefault("params", {})
        penalty = round(min(0.12, 0.03 * severity), 4)
        params["enter_long_delta"] = round(
            max(-0.15, float(params.get("enter_long_delta") or 0) - penalty),
            4,
        )
        if severity >= 0.85 and ex >= min_exits + 1:
            params["pause_long"] = True
            brakes.append(f"{sym}:pause_long pnl={pnl:.2f} ex={ex}")
        else:
            brakes.append(f"{sym}:enter_delta-={penalty} pnl={pnl:.2f}")

        # Disable worst session pattern when lifetime stats justify it.
        lps = doc.get("lifetime_pattern_stats") or doc.get("pattern_stats") or {}
        worst_pat = None
        worst_pnl = 0.0
        for pat, st in lps.items():
            if not isinstance(st, dict):
                continue
            sp = float(st.get("sum_pnl_usd") or 0)
            if sp < worst_pnl:
                worst_pnl = sp
                worst_pat = pat
        if worst_pat and pnl < -min_loss * 1.2:
            disabled = set(params.get("disable_patterns") or [])
            if worst_pat not in disabled:
                disabled.add(worst_pat)
                params["disable_patterns"] = sorted(disabled)
                brakes.append(f"{sym}:disable_pattern:{worst_pat}")

        notes = list(doc.get("si_notes") or [])
        notes.append(f"session_brake:{pnl:.2f} severity={severity:.2f}")
        doc["si_notes"] = notes[-8:]
        _save_learned(component, sym, doc)

    return {"component": component, "brakes": brakes, "min_loss_usd": round(min_loss, 3)}


def _unified_si_state_path() -> Path:
    return _data_dir() / "unified_ai" / "si_adaptive.json"


def load_unified_si_state() -> dict[str, Any]:
    p = _unified_si_state_path()
    if not p.exists():
        return {"symbol_actions": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"symbol_actions": {}}
    except Exception:
        return {"symbol_actions": {}}


def save_unified_si_state(doc: dict[str, Any]) -> None:
    p = _unified_si_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_utc"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def unified_symbol_blocked(symbol: str) -> tuple[bool, str | None]:
    """Read SI adaptive blocks (post-trim re-entry halt)."""
    sym = str(symbol or "").strip().upper()
    rec = (load_unified_si_state().get("symbol_actions") or {}).get(sym)
    if not isinstance(rec, dict):
        return False, None
    if rec.get("block_entries"):
        return True, str(rec.get("reason") or "si_adaptive_block")
    return False, None


def _fortress_portfolio_snapshot() -> dict[str, Any]:
    try:
        from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs, is_alpaca_paper

        key, sec = alpaca_credentials()
        if not key or not sec:
            return {"connected": False}
        from alpaca.trading.client import TradingClient

        tc = TradingClient(key, sec, **alpaca_trading_client_kwargs())
        acct = tc.get_account()
        positions = []
        for p in tc.get_all_positions()[:40]:
            positions.append(
                {
                    "symbol": getattr(p, "symbol", ""),
                    "qty": float(getattr(p, "qty", 0) or 0),
                    "unrealized_pl": float(getattr(p, "unrealized_pl", 0) or 0),
                }
            )
        return {
            "connected": True,
            "paper": is_alpaca_paper(),
            "equity": float(acct.equity),
            "positions": positions,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)[:120]}


def apply_unified_loser_management(*, dry_run: bool | None = None) -> dict[str, Any]:
    """
    Adaptive partial trim + entry block on unified-book losers exceeding equity % threshold.
    """
    if not _enabled():
        return {"skipped": "adaptive_disabled"}
    if str(os.getenv("FORTRESS_SI_UNIFIED_LOSER_TRIM", "1")).strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"skipped": "unified_loser_trim_disabled"}

    from agents.unified_ai_agent import _dry_run, act, observe

    pf = _fortress_portfolio_snapshot()
    if not pf.get("connected"):
        return {"skipped": "alpaca_not_connected", "detail": pf.get("error")}

    equity = float(pf.get("equity") or 0)
    if equity <= 0:
        return {"skipped": "no_equity"}

    trim_pct = float(_get_cap("unified_loser_trim_pct_equity", 0.05))
    trim_frac = float(_get_cap("unified_loser_trim_fraction", 0.25))
    actions: list[str] = []
    state = load_unified_si_state()
    sym_actions = state.setdefault("symbol_actions", {})

    if dry_run is None:
        dry_run = _dry_run()

    positions = pf.get("positions") or []
    obs = observe()
    for pos in positions:
        sym = str(pos.get("symbol") or getattr(pos, "symbol", "") or "").upper()
        upl = float(pos.get("unrealized_pl") or getattr(pos, "unrealized_pl", 0) or 0)
        qty = float(pos.get("qty") or getattr(pos, "qty", 0) or 0)
        if qty <= 0 or upl >= 0:
            continue
        loss_pct = abs(upl) / equity
        if loss_pct < trim_pct:
            continue
        severity = min(1.0, loss_pct / max(trim_pct, 0.001))
        sell_frac = min(0.5, trim_frac * (0.5 + severity * 0.5))
        sell_qty = max(1, int(qty * sell_frac))
        if dry_run:
            actions.append(f"{sym}:dry_trim {sell_qty}/{int(qty)} upl={upl:.2f}")
            sym_actions[sym] = {
                "block_entries": True,
                "reason": "si_adaptive_loser_dry_run",
                "last_unrealized_pl": upl,
            }
            continue
        try:
            decision = {
                "symbol": sym,
                "action": "exit_position",
                "qty": sell_qty,
                "confidence": 1.0,
                "reasoning": "si_adaptive_loser_trim",
            }
            result = act(decision, obs, {})
            if result.get("executed"):
                actions.append(f"{sym}:trim {sell_qty} upl={upl:.2f}")
                sym_actions[sym] = {
                    "block_entries": True,
                    "reason": "si_adaptive_loser_trim",
                    "trim_qty": sell_qty,
                    "last_unrealized_pl": upl,
                }
            else:
                actions.append(f"{sym}:trim_blocked {result.get('detail')}")
        except Exception as e:
            actions.append(f"{sym}:error {str(e)[:80]}")

    save_unified_si_state(state)
    return {"actions": actions, "trim_pct_equity": trim_pct, "dry_run": dry_run}


def adaptive_classic_fill_recency_max() -> float:
    return float(_get_cap("classic_fill_recency_days_max", 7.0))


def run_adaptive_si_cycle(
    *,
    gaps: list[dict[str, Any]] | None = None,
    edge_context: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Called from RTH SI after capability review metrics are available."""
    gaps = gaps or []
    out: dict[str, Any] = {"ok": True}
    edge_ctx = edge_context or {}

    for component in ("skim_swarm", "infra_swarm"):
        sc = (edge_ctx.get(component) or {}).get("scorecard") or {}
        roll_fix = apply_rolling_aware_edge_autofix(component, sc, gaps=gaps)
        brakes = apply_symbol_session_brakes(component)
        out[component] = {"rolling_edge": roll_fix, "symbol_brakes": brakes}

    out["unified_losers"] = apply_unified_loser_management()
    return out
