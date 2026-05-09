"""
Process external P&amp;L ledger lines into structured beliefs (checkpointed).
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger("belief_trade_hook")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def checkpoint_path() -> Path:
    return _root() / "data" / "beliefs" / "ledger_checkpoint.json"


def default_ledger_path() -> Path:
    raw = (os.environ.get("FORTRESS_AI_EXTERNAL_LEDGER_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path("/home/ubuntu/trading-bot/data/pnl_ledger.jsonl")


def _regime_from_env_files() -> str:
    p = (os.environ.get("FORTRESS_AI_REGIME_JSON") or "").strip()
    candidates = [Path(p)] if p else []
    candidates.append(Path("/home/ubuntu/trading-bot/data/daily_risk_params.json"))
    for c in candidates:
        try:
            if c.exists():
                doc = json.loads(c.read_text(encoding="utf-8"))
                if isinstance(doc, dict) and doc.get("regime"):
                    return str(doc.get("regime"))
        except Exception:
            logger.warning("could not read regime from %s", c)
    return "UNKNOWN"


def _strategy_default() -> str:
    return str(os.getenv("FORTRESS_AI_DEFAULT_STRATEGY", "mean_reversion")).strip() or "mean_reversion"


def _load_checkpoint() -> dict[str, Any]:
    p = checkpoint_path()
    if not p.exists():
        return {"lines_processed": 0}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"lines_processed": 0}


def _save_checkpoint(doc: dict[str, Any]) -> None:
    checkpoint_path().parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def process_external_ledger(observation: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Read new lines from external JSONL ledger; create beliefs. Safe no-op if disabled/missing file.
    """
    if str(os.getenv("FORTRESS_AI_BELIEF_LEDGER_HOOK", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    ledger = default_ledger_path()
    if not ledger.exists():
        logger.info("external ledger not found at %s — skip belief hook", ledger)
        return {"ok": True, "skipped": True, "reason": "ledger_missing"}

    try:
        from knowledge.intel import infer_regime, infer_strategy

        obs = observation if isinstance(observation, dict) else {}
        regime = _regime_from_env_files()
        if regime == "UNKNOWN":
            regime = infer_regime(
                {
                    "vix_last": obs.get("vix_last"),
                    "spy_last": obs.get("spy_last"),
                }
            )
        state_stub = {"beliefs": {}}
        strategy = infer_strategy(obs, state_stub)
    except Exception:
        logger.exception("regime/strategy inference fallback")
        regime = _regime_from_env_files()
        strategy = _strategy_default()

    cp = _load_checkpoint()
    start = int(cp.get("lines_processed") or 0)

    lines: list[str]
    try:
        raw_txt = ledger.read_text(encoding="utf-8", errors="replace")
        lines = raw_txt.splitlines()
    except Exception:
        logger.exception("failed reading ledger")
        return {"ok": False, "error": "read_failed"}

    new_lines = lines[start:]
    processed = 0
    errors: list[str] = []

    from utils.belief_manager import add_or_update_belief

    for ln in new_lines:
        ln = ln.strip()
        if not ln:
            processed += 1
            continue
        try:
            row = json.loads(ln)
        except Exception:
            logger.warning("skip malformed ledger line: %s", ln[:120])
            processed += 1
            continue
        try:
            sym = str(row.get("ticker") or row.get("underlying_ticker") or "").strip().upper()
            pnl = float(row.get("pnl") or 0.0)
            pnl_pct = row.get("pnl_pct")
            pnl_pct_f = float(pnl_pct) if pnl_pct is not None else None
            strat = str(row.get("strategy") or strategy).strip() or _strategy_default()
            conf = float(row.get("entry_signal_confidence") or row.get("confidence") or 0.5)
            hold_h = float(row.get("hold_duration_hours") or 0.0)
            reg = str(row.get("regime_at_entry") or regime)
            add_or_update_belief(
                symbol=sym or "UNKNOWN",
                regime_at_entry=reg,
                strategy_used=strat,
                entry_signal_confidence=conf,
                pnl=pnl,
                pnl_pct=pnl_pct_f,
                hold_duration_hours=hold_h,
            )
        except Exception:
            errors.append(traceback.format_exc()[:400])
            logger.exception("belief update failed for ledger row")
        processed += 1

    cp["lines_processed"] = start + processed
    cp["last_ledger_path"] = str(ledger)
    cp["last_run_errors"] = errors[:5]
    _save_checkpoint(cp)

    return {"ok": True, "processed_lines": processed, "checkpoint": cp}


if __name__ == "__main__":
    print(json.dumps(process_external_ledger(), indent=2))
