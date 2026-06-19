"""
Deterministic adaptive profit-taking for unified AI — runs before LLM each cycle.

Regime- and RSI-aware exits aligned with mean-reversion entry (RSI dip buy → RSI
overbought take-profit). Does not weaken pre_trade_gate or immutable caps.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = _ROOT / "config" / "unified_position_exit.yaml"
_RSI_CACHE: dict[tuple[str, int], tuple[float, float | None]] = {}
_RSI_CACHE_TTL = float(os.environ.get("FORTRESS_UNIFIED_EXIT_RSI_CACHE_SEC", "90") or 90)


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


def load_config() -> dict[str, float | bool]:
    defaults: dict[str, float | bool] = {
        "min_profit_usd": 20.0,
        "min_profit_pct": 0.005,
        "eod_flatten_minutes_before_close": 20.0,
        "rsi_period": 14.0,
        "rsi_extreme_min_usd": 8.0,
        "rsi_extreme_level": 72.0,
        "stop_loss_pct": 0.02,
        "stop_loss_enabled": True,
        "bear_min_usd_scale": 0.75,
        "bear_min_pct_scale": 0.75,
        "bear_rsi_exit_delta": 3.0,
        "bear_eod_scale": 1.25,
        "bear_stop_tighten": 0.85,
        "bull_min_usd_scale": 1.15,
        "bull_min_pct_scale": 1.1,
        "bull_rsi_exit_delta": 3.0,
        "close_phase_eod_min": 25.0,
    }
    path = config_path()
    if not path.is_file():
        return defaults
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            return defaults
        out: dict[str, float | bool] = dict(defaults)
        for key in defaults:
            if doc.get(key) is not None:
                if isinstance(defaults[key], bool):
                    out[key] = bool(doc[key])
                else:
                    out[key] = float(doc[key])
        return out
    except Exception:
        return defaults


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def fetch_symbol_rsi(symbol: str, *, period: int = 14) -> float | None:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    cache_key = (sym, period)
    now = time.time()
    cached = _RSI_CACHE.get(cache_key)
    if cached and now - cached[0] < _RSI_CACHE_TTL:
        return cached[1]
    rsi_val: float | None = None
    try:
        import yfinance as yf

        ticker = yf.Ticker(sym)
        hist = ticker.history(period="5d", interval="5m")
        if hist is None or len(hist) < period + 1:
            hist = ticker.history(period="1mo", interval="1d")
        if hist is not None and len(hist) >= period + 1:
            closes = [float(x) for x in hist["Close"].tolist() if x == x]
            rsi_val = compute_rsi(closes, period)
    except Exception:
        rsi_val = None
    _RSI_CACHE[cache_key] = (now, rsi_val)
    return rsi_val


def resolve_adaptive_thresholds(
    observation: dict[str, Any] | None,
    cfg: dict[str, float | bool] | None = None,
) -> dict[str, Any]:
    """Scale profit/RSI/stop thresholds by VIX regime and market consciousness."""
    from knowledge.intel import infer_regime
    from utils.tunable_overrides import (
        get_rsi_entry_threshold_int,
        get_rsi_exit_threshold_int,
        load_overrides,
    )

    base = cfg or load_config()
    obs = observation if isinstance(observation, dict) else {}

    min_usd = float(base["min_profit_usd"])
    min_pct = float(base["min_profit_pct"])
    eod_min = float(base["eod_flatten_minutes_before_close"])
    stop_loss = float(base["stop_loss_pct"])
    rsi_exit = get_rsi_exit_threshold_int()
    rsi_entry = get_rsi_entry_threshold_int()
    regime = infer_regime(obs)

    if regime == "BEAR_TREND":
        min_usd *= float(base["bear_min_usd_scale"])
        min_pct *= float(base["bear_min_pct_scale"])
        rsi_exit -= int(float(base["bear_rsi_exit_delta"]))
        eod_min *= float(base["bear_eod_scale"])
        stop_loss *= float(base["bear_stop_tighten"])
    elif regime == "BULL_TREND":
        min_usd *= float(base["bull_min_usd_scale"])
        min_pct *= float(base["bull_min_pct_scale"])
        rsi_exit += int(float(base["bull_rsi_exit_delta"]))

    mc = obs.get("market_consciousness")
    if isinstance(mc, dict):
        posture = str(mc.get("posture") or mc.get("risk_posture") or "").lower()
        if posture in ("defensive", "risk_off", "caution", "reduce"):
            min_usd *= 0.85
            min_pct *= 0.9
            rsi_exit -= 2
        slot = mc.get("temporal_slot")
        if isinstance(slot, dict) and str(slot.get("rth_phase") or "") == "close":
            eod_min = max(eod_min, float(base["close_phase_eod_min"]))

    overrides = load_overrides()
    if overrides.get("min_profit_usd") is not None:
        try:
            min_usd = float(overrides["min_profit_usd"])
        except (TypeError, ValueError):
            pass
    if overrides.get("min_profit_pct") is not None:
        try:
            min_pct = float(overrides["min_profit_pct"])
        except (TypeError, ValueError):
            pass
    if overrides.get("rsi_exit_threshold") is not None:
        try:
            rsi_exit = int(float(overrides["rsi_exit_threshold"]))
        except (TypeError, ValueError):
            pass

    rsi_exit = max(55, min(78, rsi_exit))
    min_usd = max(5.0, min_usd)
    min_pct = max(0.002, min_pct)
    stop_loss = max(0.01, min(0.04, stop_loss))

    return {
        "min_profit_usd": min_usd,
        "min_profit_pct": min_pct,
        "eod_flatten_minutes_before_close": eod_min,
        "rsi_exit_threshold": rsi_exit,
        "rsi_entry_threshold": rsi_entry,
        "stop_loss_pct": stop_loss,
        "stop_loss_enabled": bool(base.get("stop_loss_enabled", True)),
        "rsi_extreme_min_usd": float(base["rsi_extreme_min_usd"]),
        "rsi_extreme_level": int(float(base["rsi_extreme_level"])),
        "rsi_period": int(float(base["rsi_period"])),
        "regime": regime,
    }


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


def _plan_for_position(
    row: dict[str, Any],
    *,
    thresholds: dict[str, Any],
    mins_to_close: float | None,
    rsi: float | None,
) -> dict[str, Any] | None:
    sym = str(row.get("sym") or row.get("symbol") or "").strip().upper()
    qty = _position_qty(row)
    if not sym or qty <= 0:
        return None

    upl = _unrealized_usd(row)
    upl_pct = _unrealized_pct(row)
    eod_min = float(thresholds["eod_flatten_minutes_before_close"])
    min_usd = float(thresholds["min_profit_usd"])
    min_pct = float(thresholds["min_profit_pct"])
    rsi_exit = int(thresholds["rsi_exit_threshold"])
    rsi_entry = int(thresholds["rsi_entry_threshold"])
    stop_loss = float(thresholds["stop_loss_pct"])
    regime = str(thresholds.get("regime") or "NEUTRAL_RANGING")

    eod_window = mins_to_close is not None and 0 <= mins_to_close <= eod_min

    if thresholds.get("stop_loss_enabled") and upl_pct <= -stop_loss:
        reason = (
            f"adaptive_stop_loss upl_pct={upl_pct:.4f}<=-{stop_loss:.4f} "
            f"regime={regime} rsi={rsi if rsi is not None else 'na'}"
        )
        marker = "adaptive_stop_loss"
    elif eod_window and upl > 0:
        reason = (
            f"eod_profit_flatten upl=${upl:.2f} mins_to_close={mins_to_close:.0f} "
            f"regime={regime} adaptive_eod_min={eod_min:.0f}"
        )
        marker = "eod_profit_flatten"
    elif rsi is not None and rsi >= int(thresholds["rsi_extreme_level"]) and upl >= float(
        thresholds["rsi_extreme_min_usd"]
    ):
        reason = (
            f"rsi_extreme_overbought rsi={rsi:.1f}>={thresholds['rsi_extreme_level']} "
            f"upl=${upl:.2f} regime={regime}"
        )
        marker = "rsi_extreme_overbought"
    elif rsi is not None and rsi >= rsi_exit and upl >= max(5.0, min_usd * 0.25):
        reason = (
            f"rsi_overbought_exit rsi={rsi:.1f}>={rsi_exit} "
            f"(entry<{rsi_entry}) upl=${upl:.2f} upl_pct={upl_pct:.4f} regime={regime}"
        )
        marker = "rsi_overbought_exit"
    elif upl >= min_usd:
        reason = (
            f"take_profit_usd upl=${upl:.2f}>={min_usd:.2f} "
            f"regime={regime} rsi={rsi if rsi is not None else 'na'}"
        )
        marker = "take_profit_usd"
    elif upl_pct >= min_pct and upl > 0:
        reason = (
            f"take_profit_pct upl_pct={upl_pct:.4f}>={min_pct:.4f} "
            f"regime={regime} rsi={rsi if rsi is not None else 'na'}"
        )
        marker = "take_profit_pct"
    else:
        return None

    return {
        "sym": sym,
        "qty": qty,
        "unrealized_pl": round(upl, 4),
        "unrealized_plpc": round(upl_pct, 6),
        "rsi": round(rsi, 2) if rsi is not None else None,
        "adaptive_thresholds": {
            "min_profit_usd": min_usd,
            "min_profit_pct": min_pct,
            "rsi_exit_threshold": rsi_exit,
            "regime": regime,
        },
        "reason": reason,
        "block_reason_marker": marker,
    }


def plan_profit_exits(
    positions: list[dict[str, Any]] | None,
    observation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return full-qty exit plans for adaptive profit, RSI, and EOD flatten rules."""
    if not enabled():
        return []
    cfg = load_config()
    thresholds = resolve_adaptive_thresholds(observation, cfg)
    mins_to_close = _minutes_to_rth_close()
    rsi_period = int(thresholds["rsi_period"])

    plans: list[dict[str, Any]] = []
    for row in positions or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("sym") or row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        rsi = fetch_symbol_rsi(sym, period=rsi_period)
        plan = _plan_for_position(
            row,
            thresholds=thresholds,
            mins_to_close=mins_to_close,
            rsi=rsi,
        )
        if plan:
            plans.append(plan)

    plans.sort(key=lambda x: float(x.get("unrealized_pl") or 0), reverse=True)
    return plans


def run_profit_exit_pass(
    observation: dict[str, Any],
    *,
    act_fn: Any,
    refresh_observation: Any | None = None,
) -> dict[str, Any]:
    """
    Execute adaptive profit exits via act_fn(decision, observation, usage).

    Returns summary with per-symbol act results.
    """
    plans = plan_profit_exits(observation.get("positions") or [], observation)
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
            "market_assessment": "unified_adaptive_exit_monitor",
            "parameters": {
                "symbol": plan["sym"],
                "qty": plan["qty"],
                "rationale": plan["reason"],
                "rsi": plan.get("rsi"),
                "adaptive_thresholds": plan.get("adaptive_thresholds"),
            },
            "expected_outcome": "realize profit or cut loss per adaptive/RSI rules",
            "_profit_exit_monitor": True,
        }
        usage = {"model": "adaptive_exit_monitor", "cost_usd": 0.0}
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
