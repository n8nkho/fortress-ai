#!/usr/bin/env python3
"""End-of-session DeepSeek review of skim swarm logs and learnings (read-only advisory)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from agents.skim_swarm.eod import session_date_et
from agents.skim_swarm.pnl import compute_pnl_summary, learned_symbol_snapshot
from agents.skim_swarm.session_reconcile import reconcile_session_stats
from agents.skim_swarm.symbol_learning import load_learned, learned_path
from agents.unified_ai_agent import call_deepseek
from scripts.skim_swarm_analyze import analyze
from utils.skim_swarm_config import improve_interval_exits, improve_min_exits, swarm_data_dir, universe


def _learned_summary() -> list[dict]:
    rows: list[dict] = []
    for sym in universe():
        p = learned_path(sym)
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        snap = learned_symbol_snapshot(raw)
        caus = raw.get("causation") or {}
        rows.append(
            {
                "symbol": sym,
                "session_stats": snap.get("stats"),
                "params": snap.get("params"),
                "improvement_cycles": (snap.get("stats") or {}).get("improvement_cycles"),
                "eliminated_keys": len(caus.get("eliminated_keys") or []),
                "top_losers": (caus.get("top_losers") or [])[:3],
                "notes": (raw.get("notes") or [])[-5:],
            }
        )
    rows.sort(key=lambda r: float((r.get("session_stats") or {}).get("sum_pnl_usd") or 0))
    return rows


def build_review_bundle(*, minutes: int = 240, reconcile: bool = True) -> dict:
    if reconcile:
        reconcile_report = reconcile_session_stats(force=False)
    else:
        reconcile_report = {"skipped": True}

    pnl = compute_pnl_summary()
    analysis = analyze(minutes=minutes)
    learned = _learned_summary()

    reconcile_path = swarm_data_dir() / "session_reconcile.json"
    prior_reconcile = None
    if reconcile_path.exists():
        try:
            prior_reconcile = json.loads(reconcile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "session_date_et": session_date_et(),
        "improve_min_exits": improve_min_exits(),
        "improve_interval_exits": improve_interval_exits(),
        "reconcile": reconcile_report,
        "prior_reconcile": prior_reconcile,
        "pnl": pnl,
        "analyze_window_minutes": minutes,
        "analyze": {
            k: analysis.get(k)
            for k in (
                "window_realized_pnl_usd",
                "session_realized_pnl_usd",
                "executed_actions",
                "top_block_reasons",
                "symbol_insights",
            )
            if k in analysis
        },
        "learned": learned,
    }


def _prompt_for(bundle: dict) -> str:
    payload = json.dumps(bundle, indent=2, default=str)[:28000]
    return f"""You are an independent trading systems reviewer for a paper intraday skim swarm.

RULES:
- Per-symbol independence: no global ticker bans; suggest per-symbol pauses/tightening only.
- DeepSeek must NOT control live orders; advisory report only.
- Be direct about negative expectancy, over-churn, and broken feedback loops.

SYSTEM:
- 16 symbols, 1 share, rule-based 1m patterns, per-symbol learning + causation blocks.
- Adaptivity: param tuning after min exits, causation eliminates losing entry contexts per symbol.

DATA BUNDLE (JSON):
{payload}

Respond in markdown with sections:
## Verdict (continue paper / pause / redesign)
## Is adaptivity real or cosmetic?
## Top 3 structural issues today
## Per-symbol recommendations (symbol → action: keep | tighten | pause | investigate)
## Causation & learning audit (are gates firing? log vs learned aligned?)
## Suggested parameter policy changes (no code, operator-readable)
## What NOT to change

Keep under 900 words. Quantify where possible."""


def run_review(*, minutes: int = 240, reconcile: bool = True, use_llm: bool = True) -> dict:
    bundle = build_review_bundle(minutes=minutes, reconcile=reconcile)
    out: dict = {"ok": True, "bundle": bundle, "markdown": None, "llm": None}

    if use_llm:
        text, meta = call_deepseek(_prompt_for(bundle), max_out_tokens=1400)
        out["markdown"] = text
        out["llm"] = meta
    else:
        out["markdown"] = "_LLM review skipped (--no-llm)_"

    dd = swarm_data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    stamp = bundle["session_date_et"]
    json_path = dd / f"deepseek_review_{stamp}.json"
    md_path = dd / f"deepseek_review_{stamp}.md"
    latest_json = dd / "deepseek_review_latest.json"
    latest_md = dd / "deepseek_review_latest.md"

    json_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    md_path.write_text(str(out["markdown"]), encoding="utf-8")
    latest_json.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    latest_md.write_text(str(out["markdown"]), encoding="utf-8")

    out["paths"] = {
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="DeepSeek advisory review of skim swarm session")
    ap.add_argument("--minutes", type=int, default=240, help="Analyze window for skim_swarm_analyze")
    ap.add_argument("--no-reconcile", action="store_true", help="Skip decisions→learned reconcile")
    ap.add_argument("--no-llm", action="store_true", help="Build bundle only; skip DeepSeek call")
    args = ap.parse_args()

    result = run_review(minutes=args.minutes, reconcile=not args.no_reconcile, use_llm=not args.no_llm)
    print(json.dumps({"ok": result.get("ok"), "paths": result.get("paths"), "llm_cost_usd": (result.get("llm") or {}).get("cost_usd")}, indent=2))
    if result.get("markdown") and not args.no_llm:
        print("\n--- review ---\n")
        print(result["markdown"])


if __name__ == "__main__":
    main()
