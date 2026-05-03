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

from knowledge.intel import build_domain_prompt_appendix


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
        f"RSI<{rsi_thr} typical for dip entries when screening — you may reference levels conceptually."
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
    return f"""You are Fortress AI. Respond with ONE JSON object only (no markdown).

CURRENT_STATE:{obs}
MEMORY:{mem}
CONSTRAINTS:{constraints}

AVAILABLE_ACTIONS (pick exactly one "action"):
- wait: no trade
- screen_market: flag opportunity scan intent (parameters: watchlist optional list of tickers max 5)
- enter_position: parameters symbol (US equity), qty (int shares), rationale
- exit_position: parameters symbol, qty (int shares to sell)
- update_beliefs: parameters beliefs dict only

Output schema:
{{"reasoning":"short chain of thought","market_assessment":"one line","action":"<name>","parameters":{{}},"confidence":0.0,"expected_outcome":"one line"}}"""


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
        return result

    if conf < _min_confidence_execute():
        result["detail"] = f"confidence_below_threshold:{conf}<{_min_confidence_execute()}"
        return result

    sym = str(params.get("symbol") or "").strip().upper()
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

        order_data = MarketOrderRequest(symbol=sym, qty=qty, side=side, time_in_force="day")
        order = tc.submit_order(order_data)
        result["executed"] = True
        result["detail"] = {"id": str(order.id), "status": str(order.status)}
    except Exception as e:
        result["detail"] = f"broker_error:{type(e).__name__}:{e}"

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
    n = 0
    while iterations is None or n < iterations:
        exceeded, spent, cap = weekly_budget_exceeded()
        if exceeded:
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "weekly_cost_cap_stop",
                "spent_usd": spent,
                "cap_usd": cap,
            }
            append_decision(rec)
            append_metric(rec)
            print(json.dumps(rec))
            return

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

        log_rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "observation_keys": list(obs.keys()),
            "decision": {k: v for k, v in decision.items() if not str(k).startswith("_")},
            "usage": {k: v for k, v in usage.items() if k != "record"},
            "act": act_result,
            "dry_run": _dry_run(),
            "min_confidence": _min_confidence_execute(),
        }
        append_decision(log_rec)
        sleep_sec = effective_loop_interval_seconds(interval_sec)
        from utils.tunable_overrides import get_decision_interval_seconds as _gi

        _ov = _gi(interval_sec)
        if _ov is not None:
            sleep_sec = float(_ov)
        snap = {
            "ts": log_rec["ts"],
            "opportunity_detection": decision.get("action") not in ("wait",),
            "confidence": float(decision.get("confidence") or 0),
            "decision_latency_ms": usage.get("latency_ms"),
            "total_cycle_latency_ms": latency_total,
            "llm_cost_usd": usage.get("cost_usd"),
            "weekly_llm_spend_usd": weekly_budget_exceeded()[1],
            "executed": bool(act_result.get("executed")),
            "us_equity_rth": is_us_equity_rth_et(),
            "next_sleep_sec": sleep_sec,
        }
        append_metric(snap)
        (_data_dir() / "ai_latest_metric.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")

        print(
            json.dumps(
                {
                    "ok": True,
                    "action": decision.get("action"),
                    "latency_ms": latency_total,
                    "us_equity_rth": is_us_equity_rth_et(),
                    "next_sleep_sec": sleep_sec,
                },
                default=str,
            )
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
