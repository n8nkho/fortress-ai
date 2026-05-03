#!/usr/bin/env python3
"""
Optional batch job: derive compact lessons from recent ``ai_decisions.jsonl``.

Run from repo root:
  python3 agents/knowledge_curator.py --max 5

Does not start the agent loop. LLM extraction only if FORTRESS_AI_DOMAIN_LLM_LEARN=1.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    import os

    raw = (os.environ.get("FORTRESS_AI_DATA_DIR") or "").strip()
    return Path(raw) if raw else (_ROOT / "data")


def run_curator(*, max_process: int = 5) -> int:
    sys.path.insert(0, str(_ROOT))
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Fortress knowledge curator")
    ap.add_argument("--max", type=int, default=5, help="Max executed decisions to process")
    args = ap.parse_args()
    n = run_curator(max_process=max(1, min(args.max, 50)))
    print(json.dumps({"ok": True, "processed": n}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
