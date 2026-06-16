#!/usr/bin/env python3
"""
Fortress AI Skim Swarm — parallel intraday 1-share skims, no LLM, always-on.

Replaces standalone SPY intraday service when fortress-ai-skim-swarm is enabled.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from agents.skim_swarm.company_context import load_all_contexts
from agents.skim_swarm.coordinator import (
    EntrySlotGuard,
    apply_daily_pnl,
    count_open,
    max_open_ok,
    should_halt_new_entries,
)
from agents.skim_swarm.eod import describe_eod_phase
from agents.skim_swarm.features import build_shared_context, _fetch_bars
from agents.skim_swarm.observe import observe_account
from agents.skim_swarm.session_reconcile import reconcile_session_on_boot
from agents.skim_swarm.symbol_learning import sync_adaptive_state_on_boot
from agents.skim_swarm.state import load_swarm_state, save_swarm_state
from agents.skim_swarm.worker import run_symbol_cycle
from utils.skim_swarm_config import (
    base_interval_sec,
    dry_run,
    fast_interval_sec,
    idle_poll_sec,
    instance_name,
    max_open_positions,
    slow_lane_interval_sec,
    swarm_data_dir,
    universe,
)
from utils.swarm_runtime import held_position_symbols, refresh_universe_if_changed, wave_symbols
from utils.swarm_session_si import effective_max_open, session_cycle_interval_mult
from utils.swarm_wave_si import run_wave_health
from utils.us_equity_hours import is_us_equity_rth_et

logger = logging.getLogger("skim_swarm_agent")


def _ensure_dirs() -> None:
    swarm_data_dir().mkdir(parents=True, exist_ok=True)


def _decisions_path() -> Path:
    return swarm_data_dir() / "decisions.jsonl"


def _metrics_path() -> Path:
    return swarm_data_dir() / "metrics.jsonl"


def _latest_metric_path() -> Path:
    return swarm_data_dir() / "latest_metric.json"


def append_jsonl(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _next_sleep_sec(results: list[dict], cycle_sec: float) -> float:
    if any(r.get("slow_lane") for r in results):
        return slow_lane_interval_sec()
    if any(r.get("fast_loop") for r in results):
        return fast_interval_sec()
    return max(8.0, base_interval_sec() - min(cycle_sec * 0.5, 20.0))


def _wave_exit_pnl(results: list[dict]) -> float:
    total = 0.0
    for r in results:
        act = r.get("act") or {}
        decision = r.get("decision") or {}
        if not act.get("executed"):
            continue
        if decision.get("action") not in ("exit_position", "flatten"):
            continue
        u = (r.get("features") or {}).get("unrealized_usd")
        if u is not None:
            total += float(u)
    return round(total, 4)


def run_loop(iterations: int | None = None) -> None:
    _ensure_dirs()
    reconcile_report = reconcile_session_on_boot()
    adaptive_report = sync_adaptive_state_on_boot()
    from utils.swarm_universe_guard import purge_orphan_symbol_states

    orphan_purge = purge_orphan_symbol_states("skim_swarm")
    syms = universe()
    print(
        json.dumps(
            {
                "event": "skim_swarm_boot",
                "instance": instance_name(),
                "universe": syms,
                "dry_run": dry_run(),
                "rth": is_us_equity_rth_et(),
                "eod_phase": describe_eod_phase(),
                "session_reconcile": reconcile_report,
                "adaptive_sync": adaptive_report,
                "orphan_purge": orphan_purge,
            },
            default=str,
        ),
        flush=True,
    )
    n = 0
    while iterations is None or n < iterations:
        if not is_us_equity_rth_et():
            time.sleep(idle_poll_sec())
            n += 1
            if iterations is not None and n >= iterations:
                break
            continue

        t0 = time.perf_counter()
        account = observe_account()
        equity = account.get("equity")
        positions = account.get("positions") or {}
        open_n = count_open(positions)
        swarm = load_swarm_state()
        halt_new, halt_reason = should_halt_new_entries(swarm, positions)
        if halt_new and not swarm.get("halted"):
            swarm["halted"] = True
            swarm["halt_reason"] = halt_reason
            save_swarm_state(swarm)

        fresh, drift_event = refresh_universe_if_changed(syms, universe)
        if drift_event:
            print(json.dumps(drift_event, default=str), flush=True)
            syms = fresh
        configured = list(syms)
        owned = set(configured) | held_position_symbols(swarm_data_dir() / "state")
        syms = wave_symbols(configured, positions, context=["SPY", "SOXX"], owned_symbols=owned)
        try:
            from utils.portfolio_swarm_bias import filter_skim_wave_symbols

            syms, bias_meta = filter_skim_wave_symbols(syms, owned_symbols=owned)
            if bias_meta:
                print(json.dumps(bias_meta, default=str), flush=True)
        except Exception:
            pass
        context_syms = list(dict.fromkeys(syms + ["SPY", "SOXX"]))
        bars = _fetch_bars(context_syms)
        shared = build_shared_context(bars)
        company_ctx = load_all_contexts(syms)
        max_open = effective_max_open("skim_swarm") if max_open_ok(open_n) else 0
        entry_guard = EntrySlotGuard(open_n, max_open) if max_open > 0 else None

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(16, len(syms))) as ex:
            futs = {
                ex.submit(
                    run_symbol_cycle,
                    sym,
                    bars=bars,
                    shared=shared,
                    account=account,
                    swarm=swarm,
                    open_count=open_n,
                    max_open=max_open,
                    company_context=company_ctx.get(sym),
                    entry_guard=entry_guard,
                ): sym
                for sym in syms
            }
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    results.append(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": sym,
                            "error": f"{type(e).__name__}:{e}",
                        }
                    )

        cycle_sec = time.perf_counter() - t0
        exit_pnl = _wave_exit_pnl(results)
        if exit_pnl:
            swarm = apply_daily_pnl(swarm, exit_pnl)
            save_swarm_state(swarm)
        sleep_sec = _next_sleep_sec(results, cycle_sec) * session_cycle_interval_mult("skim_swarm")

        wave = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "wave": n,
            "eod_phase": describe_eod_phase(),
            "open_positions": open_n,
            "equity": equity,
            "cycle_sec": round(cycle_sec, 3),
            "next_sleep_sec": sleep_sec,
            "exit_pnl_usd": exit_pnl,
            "day_realized_pnl": swarm.get("day_realized_pnl"),
            "swarm_halted": bool(swarm.get("halted")),
            "results": results,
        }
        append_jsonl(_decisions_path(), wave)
        run_wave_health(
            component="skim_swarm",
            wave=n,
            swarm_halted=bool(swarm.get("halted")),
            results=results,
            positions=positions,
            cached_universe=configured,
            configured_universe=configured,
            universe_fn=universe,
            day_realized_pnl=swarm.get("day_realized_pnl"),
            owned_symbols=owned,
        )
        metric = {
            "ts": wave["ts"],
            "open_positions": open_n,
            "executed_count": sum(1 for r in results if (r.get("act") or {}).get("executed")),
            "cycle_sec": wave["cycle_sec"],
            "next_sleep_sec": sleep_sec,
        }
        append_jsonl(_metrics_path(), metric)
        _latest_metric_path().write_text(
            json.dumps(
                {**metric, "universe": syms, "configured_universe": configured, "dry_run": dry_run()},
                indent=2,
            )
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "wave": n,
                    "executed": metric["executed_count"],
                    "open": open_n,
                    "cycle_sec": metric["cycle_sec"],
                    "sleep_sec": sleep_sec,
                },
                default=str,
            ),
            flush=True,
        )

        n += 1
        if iterations is not None and n >= iterations:
            break
        time.sleep(sleep_sec)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Fortress AI skim swarm")
    ap.add_argument("--once", action="store_true", help="Single wave then exit")
    args = ap.parse_args()
    run_loop(iterations=1 if args.once else None)


if __name__ == "__main__":
    main()
