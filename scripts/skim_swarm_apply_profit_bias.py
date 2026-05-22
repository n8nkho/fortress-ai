#!/usr/bin/env python3
"""Loosen params on session winners so gains can accumulate (per-symbol only)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from utils.env_load import load_fortress_dotenv

load_fortress_dotenv(_ROOT)

from agents.skim_swarm.symbol_learning import load_learned, save_learned
from utils.skim_swarm_config import normalize_symbol, runtime_overrides, swarm_data_dir

# Session leaders / near-breakeven with good win rate — ease entries, modestly widen targets.
PROFIT_BIAS: dict[str, dict[str, Any]] = {
    "SOXX": {
        "enter_long_delta": -0.03,
        "enter_short_delta": -0.02,
        "target_mult": 1.10,
        "note": "session leader +$1.17 — favor entries, let targets run",
    },
    "NASA": {
        "enter_long_delta": -0.04,
        "enter_short_delta": -0.02,
        "target_mult": 1.08,
        "note": "69% win rate — ease entries",
    },
    "AMZN": {
        "enter_long_delta": -0.03,
        "enter_short_delta": -0.02,
        "target_mult": 1.06,
        "note": "67% win rate pre-review — restore edge after R:R fix",
    },
    "MSFT": {
        "enter_long_delta": 0.04,
        "note": "post-review +$0.23 — undo over-tighten on longs",
    },
    "PLTR": {
        "enter_long_delta": -0.02,
        "enter_short_delta": -0.02,
        "target_mult": 1.05,
        "note": "59% win rate — slight bias toward entries",
    },
}


def apply_profit_bias(*, dry_run: bool = False) -> dict[str, Any]:
    dd = swarm_data_dir()
    ov_path = dd / "runtime_overrides.json"
    ov = dict(runtime_overrides())
    ov["profit_bias_applied_utc"] = datetime.now(timezone.utc).isoformat()
    ov["min_cooldown_sec"] = min(float(ov.get("min_cooldown_sec") or 120), 75.0)
    ov["cooldown_mult_boost"] = min(float(ov.get("cooldown_mult_boost") or 1.1), 1.0)

    updates: list[dict[str, Any]] = []
    for sym, action in PROFIT_BIAS.items():
        sym = normalize_symbol(sym)
        learned = load_learned(sym)
        prior = dict(learned.get("params") or {})
        params = learned.setdefault("params", {})
        changes: list[str] = []
        for key, val in action.items():
            if key == "note":
                continue
            params[key] = val
            changes.append(f"{key}={val}")
        note = action.get("note")
        if note:
            learned["notes"] = (learned.get("notes") or [])[-10:] + [f"profit_bias:{note}"]
        if not dry_run:
            save_learned(sym, learned)
        updates.append({"symbol": sym, "changes": changes, "prior_params": prior})

    if not dry_run:
        ov_path.write_text(json.dumps(ov, indent=2), encoding="utf-8")

    return {"ok": True, "dry_run": dry_run, "updates": updates, "runtime_overrides_path": str(ov_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply profit bias to skim swarm winners")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(apply_profit_bias(dry_run=args.dry_run), indent=2))


if __name__ == "__main__":
    main()
