#!/usr/bin/env python3
"""
SPY/DIA intraday agent — same-day ladder trades, EOD flat, max exposure cap.

Separate from unified_ai_agent: own data dir, schedule, and systemd unit.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

try:
    from utils.env_load import load_fortress_dotenv

    load_fortress_dotenv(_ROOT)
except Exception:
    pass

from agents.spy_intraday.act import act
from agents.spy_intraday.eod import is_force_flatten_window, session_date_et
from agents.spy_intraday.observe import observe
from agents.spy_intraday.reason import reason
from agents.spy_intraday.schedule import effective_loop_seconds, should_idle
from utils.api_costs import weekly_llm_budget_status
from utils.spy_agent_config import dry_run, index_symbol, instance_name, spy_data_dir
from utils.spy_agent_runtime import consume_on_demand_cycle, sleep_until_wake
from utils.us_equity_hours import is_us_equity_rth_et

logger = logging.getLogger("spy_intraday_agent")


def _ensure_dirs() -> None:
    spy_data_dir().mkdir(parents=True, exist_ok=True)


def _decisions_path() -> Path:
    return spy_data_dir() / "decisions.jsonl"


def _metrics_path() -> Path:
    return spy_data_dir() / "metrics.jsonl"


def _latest_metric_path() -> Path:
    return spy_data_dir() / "latest_metric.json"


def append_jsonl(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def write_latest_metric(record: dict) -> None:
    _latest_metric_path().write_text(json.dumps(record, indent=2), encoding="utf-8")


def run_loop(iterations: int | None = None) -> None:
    _ensure_dirs()
    print(
        json.dumps(
            {
                "event": "spy_intraday_boot",
                "instance": instance_name(),
                "symbol": index_symbol(),
                "dry_run": dry_run(),
                "rth": is_us_equity_rth_et(),
                "session_date_et": session_date_et(),
            },
            default=str,
        ),
        flush=True,
    )
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
            append_jsonl(_decisions_path(), rec)
            print(json.dumps(rec), flush=True)
            return

        on_demand = consume_on_demand_cycle()
        if should_idle(on_demand=on_demand):
            if iterations is not None:
                print(json.dumps({"ok": True, "action": "idle", "reason": "off_hours_or_weekend"}), flush=True)
                break
            sleep_until_wake(60.0)
            continue
        degrade_llm = bool(budget.get("should_degrade_llm"))

        t0 = time.perf_counter()
        obs = observe()

        if is_force_flatten_window():
            pos = obs.get("position") or {}
            if int(pos.get("qty") or 0) > 0:
                decision = {
                    "action": "flatten_all",
                    "confidence": 1.0,
                    "reasoning": "EOD force flatten window",
                    "market_assessment": "mandatory flat",
                }
                usage: dict = {}
            else:
                decision = {"action": "wait", "confidence": 1.0, "reasoning": "EOD flat already"}
                usage = {}
        else:
            try:
                if degrade_llm:
                    from agents.spy_intraday.reason import reason_heuristic

                    decision, usage = reason_heuristic(obs)
                else:
                    decision, usage = reason(obs)
            except Exception as e:
                err = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "error": f"{type(e).__name__}: {e}",
                    "phase": "reason",
                }
                append_jsonl(_decisions_path(), err)
                sleep_until_wake(effective_loop_seconds())
                n += 1
                continue

        act_result = act(decision, obs)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "decision": decision,
            "act": act_result,
            "observation_summary": {
                "eod_phase": obs.get("eod_phase"),
                "exposure_usd": obs.get("exposure_usd"),
                "intraday_swell": (obs.get("market") or {}).get("intraday", {}).get("intraday_swell"),
                "futures_tone": ((obs.get("market") or {}).get("futures") or {}).get("tone"),
                "overnight_summary": ((obs.get("market") or {}).get("global_sessions") or {}).get(
                    "overnight_summary"
                ),
            },
        }
        append_jsonl(_decisions_path(), row)

        metric = {
            "ts": row["ts"],
            "latency_ms": latency_ms,
            "action": decision.get("action"),
            "executed": act_result.get("executed"),
            "cost_usd": usage.get("cost_usd"),
            "next_sleep_sec": effective_loop_seconds(),
            "us_equity_rth": is_us_equity_rth_et(),
        }
        append_jsonl(_metrics_path(), metric)
        write_latest_metric(metric)
        print(
            json.dumps(
                {
                    "ok": True,
                    "action": decision.get("action"),
                    "latency_ms": latency_ms,
                    "llm_degraded": degrade_llm,
                },
                default=str,
            ),
            flush=True,
        )

        try:
            from agents.spy_self_improvement_engine import get_spy_si_engine

            si = get_spy_si_engine()
            si_out = si.maybe_improve_after_cycle()
            if si_out:
                print(json.dumps({"event": "spy_self_improvement", "result": si_out}, default=str))
            mon = si.monitor_performance()
            if mon:
                print(json.dumps({"event": "spy_si_monitor", "result": mon}, default=str))
        except Exception as e:
            logger.exception("spy self-improvement hook failed: %s", e)

        sleep_until_wake(effective_loop_seconds())
        n += 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Fortress AI SPY/DIA intraday agent")
    ap.add_argument("--once", action="store_true", help="Run one cycle then exit")
    args = ap.parse_args()
    run_loop(iterations=1 if args.once else None)


if __name__ == "__main__":
    main()
