#!/usr/bin/env python3
"""Apply operator review actions to skim swarm per-symbol params (no global bans)."""
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

# DeepSeek review 2026-05-22 — per-symbol only; LLY/MA full pause via manual denylist.
REVIEW_ACTIONS: dict[str, dict[str, Any]] = {
    "CRWD": {
        "pause_long": True,
        "enter_long_delta": 0.10,
        "note": "long side -$6.15; short side positive — pause long entries",
    },
    "AVGO": {
        "enter_short_delta": 0.06,
        "target_mult": 0.75,
        "short_spy_filter": 0.0005,
        "note": "short -$3.07 on 18 exits — tighten shorts, shrink targets",
    },
    "MSFT": {
        "enter_long_delta": 0.10,
        "note": "long side negative — tighten long entries",
    },
    "NVDA": {
        "enter_short_delta": 0.06,
        "note": "short -$1.55 on 30 exits — tighten short entries",
    },
}

PAUSE_SYMBOLS = ("LLY", "MA")


def _merge_params(learned: dict[str, Any], action: dict[str, Any]) -> list[str]:
    params = learned.setdefault("params", {})
    notes: list[str] = []
    for key, val in action.items():
        if key == "note":
            continue
        params[key] = val
        notes.append(f"{key}={val}")
    return notes


def apply_actions(*, dry_run: bool = False) -> dict[str, Any]:
    dd = swarm_data_dir()
    ov_path = dd / "runtime_overrides.json"
    ov = dict(runtime_overrides())
    deny = sorted(set(normalize_symbol(s) for s in (ov.get("denylist_symbols") or [])) | set(PAUSE_SYMBOLS))
    ov["denylist_symbols"] = deny
    ov["review_actions_applied_utc"] = datetime.now(timezone.utc).isoformat()
    ov["review_actions"] = {
        "pause_symbols": list(PAUSE_SYMBOLS),
        "param_overrides": {k: {kk: vv for kk, vv in v.items() if kk != "note"} for k, v in REVIEW_ACTIONS.items()},
    }

    symbol_updates: list[dict[str, Any]] = []
    for sym, action in REVIEW_ACTIONS.items():
        sym = normalize_symbol(sym)
        learned = load_learned(sym)
        prior = dict(learned.get("params") or {})
        changes = _merge_params(learned, action)
        note = action.get("note")
        if note:
            learned["notes"] = (learned.get("notes") or [])[-10:] + [f"review_action:{note}"]
        if not dry_run:
            save_learned(sym, learned)
        symbol_updates.append({"symbol": sym, "changes": changes, "prior_params": prior})

    if not dry_run:
        ov_path.write_text(json.dumps(ov, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "dry_run": dry_run,
        "denylist_symbols": deny,
        "symbol_updates": symbol_updates,
        "runtime_overrides_path": str(ov_path),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply DeepSeek/operator per-symbol skim review actions")
    ap.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = ap.parse_args()
    report = apply_actions(dry_run=args.dry_run)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
