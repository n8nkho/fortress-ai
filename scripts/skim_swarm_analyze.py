#!/usr/bin/env python3
"""Analyze skim swarm decisions and optionally apply runtime tuning."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from agents.skim_swarm.eod import session_date_et
from agents.skim_swarm.pnl import session_daily_realized_usd
from utils.skim_swarm_config import swarm_data_dir as _default_swarm_data_dir


def _resolve_data_dir(component: str | None = None) -> Path:
    if component == "infra_swarm":
        from utils.infra_swarm_config import swarm_data_dir

        return swarm_data_dir()
    if component == "skim_swarm" or component is None:
        return _default_swarm_data_dir()
    return _default_swarm_data_dir()


def _session_symbol_pnl(data_dir: Path | None = None) -> dict[str, tuple[float, int]]:
    """Per-symbol realized P&L for the current ET session from decisions.jsonl."""
    session = session_date_et()
    per_sym: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    p = (data_dir or _default_swarm_data_dir()) / "decisions.jsonl"
    if not p.exists():
        return per_sym
    from agents.skim_swarm.pnl import _wave_session_date

    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            w = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _wave_session_date(str(w.get("ts") or "")) != session:
            continue
        for r in w.get("results") or []:
            act = r.get("act") or {}
            dec = r.get("decision") or {}
            if not act.get("executed"):
                continue
            if dec.get("action") not in ("exit_position", "flatten"):
                continue
            sym = str(r.get("symbol") or "")
            u = (r.get("features") or {}).get("unrealized_usd")
            if sym and u is not None:
                pnl, ex = per_sym[sym]
                per_sym[sym] = (pnl + float(u), ex + 1)
    return per_sym


def analyze(minutes: int = 30, *, component: str | None = None) -> dict:
    data_dir = _resolve_data_dir(component)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    p = data_dir / "decisions.jsonl"
    if not p.exists():
        return {"ok": False, "error": "no decisions log"}

    waves = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            w = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "results" not in w:
            continue
        ts = datetime.fromisoformat(str(w["ts"]).replace("Z", "+00:00"))
        if ts >= cutoff:
            waves.append(w)

    actions = Counter()
    executed = Counter()
    blocks = Counter()
    hold_reasons = Counter()
    exit_reasons = Counter()
    wins: list[float] = []
    losses: list[float] = []
    reentries_60s = 0
    open_over_max = 0
    events: dict[str, list] = defaultdict(list)
    window_sym_pnl: dict[str, float] = defaultdict(float)
    window_sym_exits: dict[str, int] = defaultdict(int)
    window_pnl = 0.0

    for w in waves:
        open_n = int(w.get("open_positions") or 0)
        if open_n > 6:
            open_over_max += 1
        for r in w.get("results") or []:
            sym = r.get("symbol")
            d = r.get("decision") or {}
            act = r.get("act") or {}
            action = d.get("action", "wait")
            actions[action] += 1
            if act.get("executed"):
                executed[action] += 1
                if action in ("enter_long", "enter_short", "exit_position", "flatten"):
                    events[sym].append((w["ts"], action))
                if action == "exit_position":
                    u = (r.get("features") or {}).get("unrealized_usd")
                    if u is not None:
                        fv = float(u)
                        window_pnl += fv
                        if sym:
                            window_sym_pnl[sym] += fv
                            window_sym_exits[sym] += 1
                        (wins if fv > 0 else losses).append(fv)
                    reason = str(d.get("reasoning") or "").split(":")[0]
                    exit_reasons[reason] += 1
            br = act.get("block_reason") or d.get("reasoning") or "?"
            blocks[str(br)[:60]] += 1
            if str(d.get("reasoning", "")).startswith("hold_"):
                hold_reasons[d["reasoning"].split(":")[0]] += 1

    for sym, evs in events.items():
        last_exit = None
        for ts, act in evs:
            if act == "exit_position":
                last_exit = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif act in ("enter_long", "enter_short") and last_exit:
                ent = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (ent - last_exit).total_seconds() < 60:
                    reentries_60s += 1
                last_exit = None

    window_per_sym = [
        (pnl, window_sym_exits[sym], sym) for sym, pnl in window_sym_pnl.items() if window_sym_exits[sym]
    ]
    session_per_sym = [
        (pnl, ex, sym) for sym, (pnl, ex) in _session_symbol_pnl(data_dir).items() if ex
    ]

    swarm_path = data_dir / "swarm_state.json"
    swarm = {}
    if swarm_path.exists():
        try:
            swarm = json.loads(swarm_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    day_realized = session_daily_realized_usd()

    report = {
        "ok": True,
        "ts": datetime.now(timezone.utc).isoformat(),
        "window_minutes": minutes,
        "waves": len(waves),
        "actions": dict(actions),
        "executed": dict(executed),
        "top_blocks": blocks.most_common(10),
        "exit_reasons": dict(exit_reasons),
        "reentries_within_60s": reentries_60s,
        "waves_open_over_6": open_over_max,
        "window_realized_pnl_usd": round(window_pnl, 4),
        "realized_sum_pnl_usd": round(window_pnl, 4),
        "session_realized_pnl_usd": round(day_realized, 4),
        "exit_wins": len(wins),
        "exit_losses": len(losses),
        "median_win": round(sorted(wins)[len(wins) // 2], 4) if wins else None,
        "median_loss": round(sorted(losses)[len(losses) // 2], 4) if losses else None,
        "worst_symbols": sorted(window_per_sym)[:5],
        "best_symbols": sorted(window_per_sym, reverse=True)[:5],
        "session_worst_symbols": sorted(session_per_sym)[:5],
        "session_best_symbols": sorted(session_per_sym, reverse=True)[:5],
        "day_realized_pnl": day_realized,
        "swarm_halted": swarm.get("halted"),
    }
    return report


def auto_tune(report: dict, *, component: str | None = None) -> dict:
    """Swarm-level churn controls only — per-symbol strategy adapts in symbol_learning."""
    overrides_path = _resolve_data_dir(component) / "runtime_overrides.json"
    overrides: dict = {}
    if overrides_path.exists():
        try:
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
        except Exception:
            overrides = {}

    changes: list[str] = []
    median_win = report.get("median_win")
    median_loss = report.get("median_loss")
    if median_win is not None and median_loss is not None and median_loss < 0:
        if abs(median_loss) > abs(median_win) * 1.3:
            if overrides.get("cooldown_mult_boost", 1.0) < 1.4:
                overrides["cooldown_mult_boost"] = round(
                    float(overrides.get("cooldown_mult_boost", 1.0)) + 0.05, 3
                )
                changes.append("cooldown_mult_boost")

    if int(report.get("reentries_within_60s") or 0) > 20:
        overrides["min_cooldown_sec"] = max(float(overrides.get("min_cooldown_sec") or 0), 120.0)
        changes.append("min_cooldown_sec=120")

    if int(report.get("waves_open_over_6") or 0) > 0:
        changes.append("warn:open_over_max_still_seen")

    symbol_insights = {
        "session_worst": report.get("session_worst_symbols") or [],
        "session_best": report.get("session_best_symbols") or [],
        "window_worst": report.get("worst_symbols") or [],
        "window_best": report.get("best_symbols") or [],
        "note": "Per-symbol params adapt via symbol_learning on each exit; no auto-denylist.",
    }
    overrides["symbol_insights"] = symbol_insights
    # Keep operator/review denylist — do not strip loss-containment symbols.

    overrides["updated_utc"] = datetime.now(timezone.utc).isoformat()
    overrides["last_report"] = report
    overrides_path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    return {"changes": changes, "symbol_insights": symbol_insights, "overrides_path": str(overrides_path)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Skim swarm performance analysis")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--component", default="skim_swarm", help="skim_swarm | infra_swarm")
    ap.add_argument("--auto-tune", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = analyze(minutes=args.minutes, component=args.component)
    out_path = _resolve_data_dir(args.component) / "tune_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    tune = None
    if args.auto_tune and report.get("ok"):
        tune = auto_tune(report, component=args.component)

    if args.json:
        print(json.dumps({"report": report, "auto_tune": tune}, indent=2))
    else:
        print(json.dumps(report, indent=2))
        if tune:
            print("auto_tune:", json.dumps(tune))

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
