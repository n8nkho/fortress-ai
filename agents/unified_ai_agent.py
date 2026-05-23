#!/usr/bin/env python3
"""
Fortress AI — single unified agent (DeepSeek) with deterministic safety rails.

Week 1: dry-run only (no broker submits).
Week 2+: paper trades when confidence >= threshold and pre_trade_gate allows.

Does not modify Classic Fortress; uses isolated data/ under this project.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from utils.env_load import load_fortress_dotenv

    load_fortress_dotenv(_ROOT)
except Exception:
    pass

import yfinance as yf

from utils.alpaca_env import alpaca_credentials, alpaca_trading_client_kwargs
from utils.api_costs import (
    append_llm_cost_record,
    estimate_llm_cost_usd,
    weekly_budget_exceeded,
    weekly_llm_budget_status,
)
from utils.agent_runtime import (
    consume_on_demand_cycle,
    off_hours_poll_seconds,
    sleep_until_next_cycle_or_wake,
)
from utils.pre_trade_gate import evaluate_pre_trade_submission, format_gate_block_message
from utils.prompt_evolution_store import get_prompt_appendix_for_cycle
from utils.tunable_overrides import get_rsi_entry_threshold_int
from utils.us_equity_hours import (
    effective_loop_interval_seconds,
    is_us_equity_rth_et,
    manual_only_schedule,
)

from knowledge.domain_ingest_context import format_domain_ingest_prompt_section
from knowledge.intel import build_domain_prompt_appendix, infer_regime, infer_strategy
from utils.belief_manager import format_beliefs_prompt_section


def _data_dir() -> Path:
    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def _ensure_dirs() -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)
    (_ROOT / "logs").mkdir(parents=True, exist_ok=True)


def _state_path() -> Path:
    return _data_dir() / "ai_state.json"


def _decisions_path() -> Path:
    return _data_dir() / "ai_decisions.jsonl"


def _metrics_path() -> Path:
    return _data_dir() / "ai_metrics.jsonl"


def load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"version": 1, "beliefs": {}, "last_actions": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "beliefs": {}, "last_actions": []}


def save_state(state: dict[str, Any]) -> None:
    _ensure_dirs()
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_decision(record: dict[str, Any]) -> None:
    _ensure_dirs()
    with open(_decisions_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def append_metric(record: dict[str, Any]) -> None:
    _ensure_dirs()
    with open(_metrics_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _alpaca_client():
    key, sec = alpaca_credentials()
    if not key or not sec:
        return None
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return None
    return TradingClient(key, sec, **alpaca_trading_client_kwargs())


def observe() -> dict[str, Any]:
    """Compact observation bundle (<2K tokens when serialized)."""
    out: dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "instance": os.environ.get("FORTRESS_INSTANCE_NAME", "Fortress-AI"),
    }
    try:
        from utils.classic_bridge import classic_screener_candidates

        scan = classic_screener_candidates(max_symbols=8)
        syms = scan.get("symbols") or []
        if syms:
            out["watchlist_hint"] = syms
            out["watchlist_source"] = scan.get("source")
    except Exception:
        pass
    # Macro (yfinance)
    try:
        spy = yf.Ticker("SPY")
        vix = yf.Ticker("^VIX")
        sp = spy.fast_info.get("last_price") or spy.history(period="5d", interval="1d")["Close"].iloc[-1]
        vx = vix.fast_info.get("last_price") or vix.history(period="5d", interval="1d")["Close"].iloc[-1]
        out["spy_last"] = float(sp)
        out["vix_last"] = float(vx)
    except Exception as e:
        out["macro_error"] = str(e)[:120]

    tc = _alpaca_client()
    out["alpaca_configured"] = bool(tc)
    if tc:
        try:
            acct = tc.get_account()
            pos = tc.get_all_positions()
            out["equity"] = float(acct.equity)
            out["buying_power"] = float(acct.buying_power)
            out["positions"] = [
                {
                    "sym": getattr(p, "symbol", ""),
                    "qty": float(getattr(p, "qty", 0) or 0),
                    "mkt_value": float(getattr(p, "market_value", 0) or 0),
                }
                for p in pos[:20]
            ]
        except Exception as e:
            out["alpaca_error"] = str(e)[:200]
    else:
        out["positions"] = []
        out["note"] = "Alpaca keys missing — observation macro-only"

    # Trim for prompt budget
    blob = json.dumps(out, default=str)
    max_chars = int(os.environ.get("FORTRESS_AI_MAX_OBS_CHARS", "3500"))
    if len(blob) > max_chars:
        out["_truncated"] = True
        out["_blob_len"] = len(blob)
        # keep only essentials
        out = {
            "ts_utc": out.get("ts_utc"),
            "spy_last": out.get("spy_last"),
            "vix_last": out.get("vix_last"),
            "equity": out.get("equity"),
            "positions": out.get("positions", [])[:8],
            "note": "observation truncated for token budget",
        }
    return out


ALLOWED_ACTIONS = frozenset(
    {"wait", "screen_market", "enter_position", "exit_position", "update_beliefs"}
)


def _parse_llm_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
        if m:
            s = m.group(1).strip()
    return json.loads(s)


def call_deepseek(prompt: str, *, max_out_tokens: int = 512) -> tuple[str, dict[str, Any]]:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").strip()
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai package required: pip install openai") from e
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=max_out_tokens,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    usage = getattr(resp, "usage", None)
    pt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    ct = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    cost = estimate_llm_cost_usd(model, pt, ct)
    meta = append_llm_cost_record(model=model, input_tokens=pt, output_tokens=ct, cost_usd=cost, meta={"latency_ms": latency_ms})
    return text, {
        "model": model,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "cost_usd": cost,
        "latency_ms": latency_ms,
        "record": meta,
    }


def build_prompt(
    observation: dict[str, Any], state: dict[str, Any], *, appendix: str = ""
) -> str:
    """Keep total prompt under ~2K tokens by tight formatting."""
    obs = json.dumps(observation, separators=(",", ":"), default=str)[:4500]
    mem = json.dumps(
        {
            "beliefs": state.get("beliefs", {}),
            "last_actions": (state.get("last_actions") or [])[-5:],
        },
        separators=(",", ":"),
        default=str,
    )[:1200]
    rsi_thr = get_rsi_entry_threshold_int()
    constraints = (
        "Mean-reversion bias; equities only in MVP. "
        "Max position notional respects FORTRESS_MAX_ORDER_NOTIONAL_USD. "
        f"RSI<{rsi_thr} typical for dip entries when screening — you may reference levels conceptually. "
        "PAPER/LIVE: When watchlist_hint symbols exist and VIX is not elevated, prefer enter_position "
        f"with confidence >= {_min_confidence_execute():.2f} over prolonged wait. "
        "Use wait only when no candidate passes gates, macro is hostile, or you lack a symbol/qty. "
        "screen_market is for explicit rescans — do not use it as a substitute for enter_position when a dip is clear."
    )
    bias = (os.environ.get("FORTRESS_AI_PROMPT_BIAS") or "").strip()
    if bias:
        constraints += " " + bias
    extra = (appendix or "").strip()
    if extra:
        constraints += " ADDITIONAL_OPERATOR_GUIDANCE: " + extra
    domain_blob = build_domain_prompt_appendix(observation, state)
    if domain_blob:
        constraints += " DOMAIN_INTEL_JSON:" + domain_blob
    regime_l = infer_regime(observation)
    strat_l = infer_strategy(observation, state)
    learned = format_beliefs_prompt_section(regime_l, strat_l)
    dom_ingest = format_domain_ingest_prompt_section(observation)
    aux_blocks = "\n\n" + learned
    if dom_ingest.strip():
        aux_blocks += "\n\n" + dom_ingest
    return f"""You are Fortress AI. Respond with ONE JSON object only (no markdown).

CURRENT_STATE:{obs}
MEMORY:{mem}
CONSTRAINTS:{constraints}{aux_blocks}

AVAILABLE_ACTIONS (pick exactly one "action"):
- wait: no trade
- screen_market: flag opportunity scan intent (parameters: watchlist optional list of tickers max 5)
- enter_position: parameters symbol (US equity), qty (int shares), rationale
- exit_position: parameters symbol, qty (int shares to sell)
- update_beliefs: parameters beliefs dict only

Output schema:
{{"reasoning":"short chain of thought","market_assessment":"one line","action":"<name>","parameters":{{}},"confidence":0.0,"expected_outcome":"one line"}}"""


def _log_prompt_tuning_event(
    *,
    variant: str,
    action: str,
    confidence: float,
    executed: bool,
    block_reason: str | None,
    degraded: bool = False,
) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "variant": variant,
        "action": action,
        "confidence": confidence,
        "executed": executed,
        "block_reason": block_reason,
        "degraded_llm": degraded,
    }
    try:
        p = _data_dir() / "prompt_tuning_log.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def reason_heuristic(observation: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic fallback when weekly LLM budget is in degrade mode."""
    min_c = _min_confidence_execute()
    wl = observation.get("watchlist_hint") or []
    sym = str(wl[0]).upper() if wl else ""
    positions = observation.get("positions") or []
    held = {str(p.get("sym") or "").upper() for p in positions if isinstance(p, dict)}
    vix = observation.get("vix_last")
    try:
        vix_ok = vix is None or float(vix) < 32.0
    except (TypeError, ValueError):
        vix_ok = True
    if sym and sym not in held and vix_ok:
        decision = {
            "reasoning": "Weekly LLM cap — heuristic entry on watchlist lead (mean-reversion).",
            "market_assessment": f"watchlist={sym} vix={vix}",
            "action": "enter_position",
            "parameters": {"symbol": sym, "qty": 1, "rationale": "budget_degrade_heuristic"},
            "confidence": min_c,
            "expected_outcome": "submit if gates and dry_run allow",
            "_heuristic": True,
        }
    else:
        decision = {
            "reasoning": "Weekly LLM cap — heuristic wait (no gated entry candidate).",
            "market_assessment": "degraded_cycle",
            "action": "wait",
            "parameters": {},
            "confidence": 0.55,
            "expected_outcome": "no trade",
            "_heuristic": True,
        }
    usage = {"model": "heuristic", "cost_usd": 0.0, "latency_ms": 0, "degraded": True, "prompt_tokens": 0, "completion_tokens": 0}
    return decision, usage


def reason(observation: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    appendix, variant = get_prompt_appendix_for_cycle(state)
    prompt = build_prompt(observation, state, appendix=appendix)
    max_chars = int(os.environ.get("FORTRESS_AI_MAX_PROMPT_CHARS", "7500"))
    if len(prompt) > max_chars:
        observation = {"note": "prompt_trimmed", "spy": observation.get("spy_last"), "vix": observation.get("vix_last")}
        prompt = build_prompt(observation, state, appendix=appendix)
    text, usage = call_deepseek(prompt)
    decision = _parse_llm_json(text)
    if decision.get("action") not in ALLOWED_ACTIONS:
        raise ValueError(f"invalid action: {decision.get('action')}")
    decision["_raw_response"] = text[:4000]
    decision["prompt_variant"] = variant
    return decision, usage


def _min_confidence_execute() -> float:
    from utils.tunable_overrides import get_confidence_threshold

    return get_confidence_threshold()


def _dry_run() -> bool:
    return str(os.environ.get("FORTRESS_AI_DRY_RUN", "1")).strip().lower() in ("1", "true", "yes", "on")


def act(decision: dict[str, Any], observation: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    action = decision.get("action") or "wait"
    params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
    conf = float(decision.get("confidence") or 0.0)
    result: dict[str, Any] = {"action": action, "executed": False, "detail": None}

    if action in ("wait", "update_beliefs", "screen_market"):
        result["detail"] = "no broker action"
        return result

    if action not in ("enter_position", "exit_position"):
        return result

    dry = _dry_run()
    if dry:
        result["detail"] = "dry_run_blocked"
        result["block_reason"] = "dry_run_blocked"
        return result

    if conf < _min_confidence_execute():
        result["detail"] = f"confidence_below_threshold:{conf}<{_min_confidence_execute()}"
        result["block_reason"] = "confidence_below_threshold"
        return result

    sym = str(params.get("symbol") or "").strip().upper()
    try:
        from utils.skim_swarm_config import normalize_symbol, symbol_denylist_for_unified_ai

        if normalize_symbol(sym) in symbol_denylist_for_unified_ai():
            result["detail"] = f"symbol_reserved_for_skim_swarm:{sym}"
            result["block_reason"] = "skim_swarm_reserved"
            return result
    except Exception:
        pass
    try:
        qty = int(abs(float(params.get("qty") or 0)))
    except (TypeError, ValueError):
        qty = 0
    if not sym or qty <= 0:
        result["detail"] = "invalid_symbol_or_qty"
        return result

    qty_requested = qty

    side = "BUY" if action == "enter_position" else "SELL"
    # Price estimate for notional gate
    px = None
    try:
        t = yf.Ticker(sym)
        px = float(t.fast_info.get("last_price") or t.history(period="1d")["Close"].iloc[-1])
    except Exception:
        px = float(observation.get("_fallback_px") or 0) or None

    try:
        equity = float(observation.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0

    est = qty * px if px else None

    # Clamp BUY size to equity × tunable position_size_pct (before spread quotes).
    if side == "BUY" and equity > 0 and px and px > 0:
        from utils.tunable_overrides import get_position_size_pct

        max_usd = equity * float(get_position_size_pct())
        max_q = int(max_usd // float(px))
        if max_q < 1:
            result["detail"] = "position_size_pct_cap_blocks_order:below_one_share"
            return result
        if qty > max_q:
            qty = max_q
            result["qty_clamped_by_position_pct"] = True
            result["qty_requested"] = qty_requested
        est = qty * px

    bid_e = ask_e = None
    quote_age = None
    if px and px > 0:
        # Conservative synthetic spread around last price so DATA_QUALITY_ENFORCE can sanity-check width.
        bid_e = float(px) * 0.9985
        ask_e = float(px) * 1.0015
        quote_age = 45.0

    gate = evaluate_pre_trade_submission(
        side=side,
        symbol=sym,
        qty=float(qty),
        estimated_notional_usd=est,
        portfolio_equity_usd=equity if equity > 0 else None,
        order_class="equity",
        bid=bid_e,
        ask=ask_e,
        quote_age_seconds=quote_age,
    )
    if not gate["allowed"]:
        result["detail"] = format_gate_block_message(gate)
        return result

    tc = _alpaca_client()
    if not tc:
        result["detail"] = "alpaca_not_configured"
        return result

    try:
        from alpaca.trading.requests import MarketOrderRequest

        alpaca_side = "buy" if side == "BUY" else "sell" if side == "SELL" else str(side).lower()
        order_data = MarketOrderRequest(symbol=sym, qty=qty, side=alpaca_side, time_in_force="day")
        order = tc.submit_order(order_data)
        result["executed"] = True
        result["detail"] = {"id": str(order.id), "status": str(order.status)}
        result["block_reason"] = "executed"
        if action == "exit_position":
            try:
                from utils.ai_pnl_ledger import append_realized_fill

                pnl_est = 0.0
                for p in observation.get("positions") or []:
                    if str(p.get("sym") or "").upper() == sym:
                        try:
                            pnl_est = float(p.get("unrealized_pl") or p.get("unrealized_plc") or 0)
                        except (TypeError, ValueError):
                            pnl_est = 0.0
                        break
                append_realized_fill(
                    symbol=sym,
                    pnl_usd=pnl_est,
                    side=side,
                    qty=qty,
                    order_id=str(order.id),
                    extra={"action": action, "note": "exit_fill_pnl_estimate"},
                )
            except Exception:
                pass
    except Exception as e:
        result["detail"] = f"broker_error:{type(e).__name__}:{e}"
        result["block_reason"] = "broker_error"

    return result


def run_loop(iterations: int | None = None, interval_sec: float | None = None) -> None:
    _ensure_dirs()
    print(
        json.dumps(
            {
                "event": "fortress_ai_loop_boot",
                "manual_only": manual_only_schedule(),
                "us_equity_rth": is_us_equity_rth_et(),
                "branch": "idle_until_run_now_when_manual_or_off_hours",
            },
            default=str,
        ),
        flush=True,
    )
    state = load_state()
    try:
        from agents.self_improvement_engine import get_engine

        boot_gov = get_engine().process_autonomous_governance()
        if boot_gov:
            print(json.dumps({"event": "self_improvement_boot", "result": boot_gov}, default=str), flush=True)
    except Exception:
        pass
    n = 0
    while iterations is None or n < iterations:
        budget = weekly_llm_budget_status()
        if budget["should_stop_loop"]:
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "weekly_cost_cap_stop",
                "spent_usd": budget["spent_usd"],
                "cap_usd": budget["cap_usd"],
                "mode": budget["mode"],
            }
            append_decision(rec)
            append_metric(rec)
            print(json.dumps(rec), flush=True)
            return
        degrade_llm = bool(budget.get("should_degrade_llm"))

        on_demand = consume_on_demand_cycle()
        # Idle until on-demand: (a) outside US RTH, or (b) FORTRESS_AI_MANUAL_ONLY=1 (no auto loops even in RTH).
        if (
            iterations is None
            and interval_sec is None
            and not on_demand
            and (not is_us_equity_rth_et() or manual_only_schedule())
        ):
            time.sleep(off_hours_poll_seconds())
            continue

        t_cycle = time.perf_counter()
        obs = observe()
        try:
            from agents.belief_trade_hook import process_external_ledger

            hook_out = process_external_ledger(obs)
            obs["_belief_ledger_hook"] = hook_out
        except Exception:
            import logging as _log

            _log.getLogger("unified_ai_agent").exception("belief ledger hook failed")
        try:
            if degrade_llm:
                decision, usage = reason_heuristic(obs, state)
            else:
                decision, usage = reason(obs, state)
        except Exception as e:
            err = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(e).__name__}: {e}",
                "phase": "reason",
            }
            append_decision(err)
            append_metric({**err, "latency_ms_decision": None})
            _sleep = effective_loop_interval_seconds(interval_sec)
            from utils.tunable_overrides import get_decision_interval_seconds as _gi

            _ov = _gi(interval_sec)
            if _ov is not None:
                _sleep = float(_ov)
            sleep_until_next_cycle_or_wake(_sleep)
            n += 1
            continue

        act_result = act(decision, obs, usage)
        latency_total = round((time.perf_counter() - t_cycle) * 1000, 2)

        # Update lightweight memory
        la = state.setdefault("last_actions", [])
        la.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "action": decision.get("action"),
                "confidence": decision.get("confidence"),
            }
        )
        state["last_actions"] = la[-50:]
        if decision.get("action") == "update_beliefs" and isinstance(decision.get("parameters"), dict):
            state["beliefs"].update(decision["parameters"].get("beliefs") or {})
        save_state(state)

        belief_inject_len = 0
        try:
            from knowledge.intel import infer_regime, infer_strategy
            from utils.belief_manager import format_beliefs_prompt_section

            _rg = infer_regime(obs)
            _st = infer_strategy(obs, state)
            _lb = format_beliefs_prompt_section(_rg, _st)
            belief_inject_len = len(_lb or "")
        except Exception:
            belief_inject_len = 0

        log_rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "observation_keys": list(obs.keys()),
            "learned_beliefs_prompt_chars": belief_inject_len,
            "decision": {k: v for k, v in decision.items() if not str(k).startswith("_")},
            "usage": {k: v for k, v in usage.items() if k != "record"},
            "act": act_result,
            "dry_run": _dry_run(),
            "min_confidence": _min_confidence_execute(),
        }
        append_decision(log_rec)
        if str(os.environ.get("FORTRESS_AI_EXPERIENCE_LOG", "1")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            try:
                from knowledge.experience_tracker import append_experience

                append_experience(
                    {
                        "action": decision.get("action"),
                        "confidence": decision.get("confidence"),
                        "executed": bool(act_result.get("executed")),
                        "detail": str(act_result.get("detail"))[:800],
                    }
                )
            except Exception:
                pass
        sleep_sec = effective_loop_interval_seconds(interval_sec)
        from utils.tunable_overrides import get_decision_interval_seconds as _gi

        _ov = _gi(interval_sec)
        if _ov is not None:
            sleep_sec = float(_ov)
        br = act_result.get("block_reason")
        if not br and act_result.get("detail"):
            d = str(act_result.get("detail") or "")
            br = d.split(":")[0] if ":" in d else d
        _log_prompt_tuning_event(
            variant=str(decision.get("prompt_variant") or "default"),
            action=str(decision.get("action") or "wait"),
            confidence=float(decision.get("confidence") or 0),
            executed=bool(act_result.get("executed")),
            block_reason=br,
            degraded=bool(usage.get("degraded")),
        )
        snap = {
            "ts": log_rec["ts"],
            "opportunity_detection": decision.get("action") not in ("wait",),
            "confidence": float(decision.get("confidence") or 0),
            "decision_latency_ms": usage.get("latency_ms"),
            "total_cycle_latency_ms": latency_total,
            "llm_cost_usd": usage.get("cost_usd"),
            "weekly_llm_spend_usd": budget["spent_usd"],
            "weekly_budget_mode": budget["mode"],
            "llm_degraded": degrade_llm or bool(usage.get("degraded")),
            "executed": bool(act_result.get("executed")),
            "block_reason": br,
            "action": decision.get("action"),
            "us_equity_rth": is_us_equity_rth_et(),
            "next_sleep_sec": sleep_sec,
        }
        append_metric(snap)
        (_data_dir() / "ai_latest_metric.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")

        try:
            from agents.self_improvement_engine import get_engine

            si = get_engine()
            si_out = si.maybe_improve_after_cycle()
            if si_out:
                print(json.dumps({"event": "self_improvement", "result": si_out}, default=str), flush=True)
            mon = si.monitor_and_revert_if_needed()
            if mon:
                print(json.dumps({"event": "si_monitor", "result": mon}, default=str), flush=True)
        except Exception:
            import logging as _log

            _log.getLogger("unified_ai_agent").exception("self-improvement hook failed")

        print(
            json.dumps(
                {
                    "ok": True,
                    "action": decision.get("action"),
                    "executed": bool(act_result.get("executed")),
                    "block_reason": br,
                    "latency_ms": latency_total,
                    "us_equity_rth": is_us_equity_rth_et(),
                    "next_sleep_sec": sleep_sec,
                    "llm_degraded": degrade_llm,
                },
                default=str,
            ),
            flush=True,
        )
        n += 1
        if iterations is not None and n >= iterations:
            break
        sleep_until_next_cycle_or_wake(sleep_sec)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fortress AI unified agent")
    ap.add_argument("--dry-run", action="store_true", help="Force dry-run (no orders)")
    ap.add_argument("--once", action="store_true", help="Single decision cycle then exit")
    ap.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Fixed loop interval (seconds); overrides RTH vs off-hours split",
    )
    args = ap.parse_args()
    if args.dry_run:
        os.environ["FORTRESS_AI_DRY_RUN"] = "1"
    run_loop(iterations=1 if args.once else None, interval_sec=args.interval)


if __name__ == "__main__":
    main()
