#!/usr/bin/env python3
"""
Batch jobs for domain intelligence:

  python3 agents/knowledge_curator.py                    # trades + decision snapshots
  python3 agents/knowledge_curator.py --trades-only      # executed broker rows only
  python3 agents/knowledge_curator.py --decisions-only   # recent agent JSON decisions
  python3 agents/knowledge_curator.py --ingest           # RSS/HTML web sources (network)
  python3 agents/knowledge_curator.py --ingest --no-web  # skip network (same as omit --ingest)

Loads ``.env`` via ``utils.env_load`` so DEEPSEEK keys apply to optional LLM paths.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    sys.path.insert(0, str(_ROOT))
    try:
        from utils.env_load import load_fortress_dotenv

        load_fortress_dotenv(_ROOT)
    except Exception:
        pass


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def run_curator_trades(*, max_process: int = 5) -> int:
    from knowledge.learning_engine import LearningEngine

    p = _data_dir() / "ai_decisions.jsonl"
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines()[-400:]
    eng = LearningEngine(_ROOT)
    n = 0
    for line in reversed(lines):
        if n >= max_process:
            break
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        dec = row.get("decision")
        act = row.get("act") if isinstance(row.get("act"), dict) else {}
        if not isinstance(dec, dict):
            continue
        if not act.get("executed"):
            continue
        params = dec.get("parameters") if isinstance(dec.get("parameters"), dict) else {}
        sym = params.get("symbol") or params.get("ticker")
        trade = {"symbol": sym, "action": dec.get("action"), "confidence": dec.get("confidence")}
        outcome = {"detail": act.get("detail"), "executed": True}
        eng.learn_from_trade_outcome(trade, outcome)
        n += 1
    return n


def run_curator_decisions(*, max_process: int = 10) -> int:
    """Snapshot recent decision rows into learnings (no domain JSON merge spam)."""
    from knowledge.learning_engine import LearningEngine

    p = _data_dir() / "ai_decisions.jsonl"
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines()[-200:]
    eng = LearningEngine(_ROOT)
    n = 0
    for line in reversed(lines):
        if n >= max_process:
            break
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        dec = row.get("decision")
        if not isinstance(dec, dict):
            continue
        if not dec.get("action"):
            continue
        ts = str(row.get("ts") or "")[:32]
        insight = " ".join(
            [
                str(dec.get("action")),
                str(dec.get("market_assessment") or "")[:120],
                str(dec.get("reasoning") or "")[:160],
            ]
        ).strip()
        eng.record_lesson(
            {
                "category": "agent_decision",
                "insight": insight[:400],
                "confidence": float(dec.get("confidence") or 0) or 0.5,
                "ts": ts,
            }
        )
        n += 1
    return n


def main() -> int:
    _bootstrap()
    ap = argparse.ArgumentParser(description="Fortress knowledge curator")
    ap.add_argument("--max", type=int, default=8, help="Cap per mode (trades / decisions)")
    ap.add_argument("--trades-only", action="store_true")
    ap.add_argument("--decisions-only", action="store_true")
    ap.add_argument("--ingest", action="store_true", help="Also run RSS/HTML web ingest (requires network)")
    ap.add_argument(
        "--ingest-only",
        action="store_true",
        help="Only web ingest (no trade/decision snapshot pass)",
    )
    ap.add_argument("--no-web", action="store_true", help="With --ingest: skip (placeholder for dry CI)")
    args = ap.parse_args()
    cap = max(1, min(args.max, 80))
    out: dict[str, Any] = {"ok": True, "trades": 0, "decisions": 0, "web": None}

    if args.ingest_only:
        if not args.no_web:
            from knowledge.web_ingest import ingest_once

            out["web"] = ingest_once(max_items_per_source=min(6, cap), max_sources=6)
        else:
            out["web"] = {"ok": True, "skipped": True, "reason": "--no-web"}
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.ingest and not args.no_web:
        from knowledge.web_ingest import ingest_once

        out["web"] = ingest_once(max_items_per_source=min(6, cap), max_sources=6)
    elif args.ingest and args.no_web:
        out["web"] = {"ok": True, "skipped": True, "reason": "--no-web"}

    if args.decisions_only:
        out["decisions"] = run_curator_decisions(max_process=cap)
    elif args.trades_only:
        out["trades"] = run_curator_trades(max_process=cap)
    else:
        out["trades"] = run_curator_trades(max_process=max(3, cap // 2))
        out["decisions"] = run_curator_decisions(max_process=cap)

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
