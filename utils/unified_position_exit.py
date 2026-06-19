"""
Deterministic profit-taking for unified AI — runs before LLM each cycle.

Does not weaken pre_trade_gate or immutable caps; uses existing act(exit_position).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _ROOT / "config" / "unified_position_exit.yaml"


def enabled() -> bool:
    return str(os.environ.get("FORTRESS_UNIFIED_POSITION_EXIT", "1")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def config_path() -> Path:
    raw = (os.environ.get("FORTRESS_UNIFIED_POSITION_EXIT_CONFIG") or "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_CONFIG


def load_config() -> dict[str, float]:
    defaults = {
        "min_profit_usd": 20.0,
        "min_profit_pct": 0.005,
        "eod_flatten_minutes_before_close": 20.0,
    }
    path = config_path()
    if not path.is_file():
        return defaults
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            return defaults
        out = dict(defaults)
        for key in defaults:
            if doc.get(key) is not None:
                out[key] = float(doc[key])
        return out
    except Exception:
        return defaults


def _position_qty(row: dict[str, Any]) -> int:
    try:
        return int(abs(float(row.get("qty") or 0)))
    except (TypeError, ValueError):
        return 0


def _unrealized_usd(row: dict[str, Any]) -> float:
    try:
        if row.get("unrealized_pl") is not None:
            return float(row["unrealized_pl"])
    except (TypeError, ValueError):
        pass
    try:
        qty = _position_qty(row)
        avg = float(row.get("avg_entry_price") or 0)
        px = float(row.get("current_price") or 0)
        if qty > 0 and avg > 0 and px > 0:
            return (px - avg) * qty
    except (TypeError, ValueError):
        pass
    return 0.0


def _unrealized_pct(row: dict[str, Any]) -> float:
    try:
        if row.get("unrealized_plpc") is not None:
            return float(row["unrealized_plpc"])
    except (TypeError, ValueError):
        pass
    try:
        avg = float(row.get("avg_entry_price") or 0)
        px = float(row.get("current_price") or 0)
        if avg > 0 and px > 0:
            return (px - avg) / avg
    except (TypeError, ValueError):
        pass
    return 0.0


def _minutes_to_rth_close() -> float | None:
    try:
        from utils.us_equity_hours import minutes_until_rth_close_et

        return minutes_until_rth_close_et()
    except Exception:
        return None


def plan_profit_exits(positions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return full-qty exit plans for positions meeting profit or EOD flatten rules."""
    if not enabled():
        return []
    cfg = load_config()
    min_usd = cfg["min_profit_usd"]
    min_pct = cfg["min_profit_pct"]
    eod_min = cfg["eod_flatten_minutes_before_close"]
    mins_to_close = _minutes_to_rth_close()
    eod_window = mins_to_close is not None and 0 <= mins_to_close <= eod_min

    plans: list[dict[str, Any]] = []
    for row in positions or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("sym") or row.get("symbol") or "").strip().upper()
        qty = _position_qty(row)
        if not sym or qty <= 0:
            continue
        upl = _unrealized_usd(row)
        upl_pct = _unrealized_pct(row)
        reason = ""
        if eod_window and upl > 0:
            reason = f"eod_profit_flatten upl=${upl:.2f} mins_to_close={mins_to_close:.0f}"
        elif upl >= min_usd:
            reason = f"take_profit_usd upl=${upl:.2f}>={min_usd:.2f}"
        elif upl_pct >= min_pct and upl > 0:
            reason = f"take_profit_pct upl_pct={upl_pct:.4f}>={min_pct:.4f}"
        else:
            continue
        plans.append(
            {
                "sym": sym,
                "qty": qty,
                "unrealized_pl": round(upl, 4),
                "unrealized_plpc": round(upl_pct, 6),
                "reason": reason,
                "block_reason_marker": "unified_profit_exit",
            }
        )
    plans.sort(key=lambda x: float(x.get("unrealized_pl") or 0), reverse=True)
    return plans


def run_profit_exit_pass(
    observation: dict[str, Any],
    *,
    act_fn: Any,
    refresh_observation: Any | None = None,
) -> dict[str, Any]:
    """
    Execute profit exits via act_fn(decision, observation, usage).

    Returns summary with per-symbol act results.
    """
    plans = plan_profit_exits(observation.get("positions") or [])
    out: dict[str, Any] = {"ok": True, "planned": len(plans), "results": [], "executed_count": 0}
    if not plans:
        out["skipped"] = "no_profit_exits"
        return out

    obs = observation
    for plan in plans:
        decision = {
            "action": "exit_position",
            "confidence": 0.95,
            "reasoning": plan["reason"],
            "market_assessment": "unified_profit_exit_monitor",
            "parameters": {
                "symbol": plan["sym"],
                "qty": plan["qty"],
                "rationale": plan["reason"],
            },
            "expected_outcome": "realize unrealized profit",
            "_profit_exit_monitor": True,
        }
        usage = {"model": "profit_exit_monitor", "cost_usd": 0.0}
        act_result = act_fn(decision, obs, usage)
        rec = {"plan": plan, "act": act_result}
        out["results"].append(rec)
        if act_result.get("executed"):
            out["executed_count"] += 1
            if refresh_observation is not None:
                try:
                    obs = refresh_observation()
                except Exception:
                    pass
    return out
