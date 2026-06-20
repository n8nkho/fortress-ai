"""
Structured trade-derived beliefs (JSON list) with Bayesian-style confidence updates.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.llm_resilience import exponential_backoff_retry

logger = logging.getLogger("belief_manager")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def beliefs_path() -> Path:
    return _root() / "data" / "beliefs" / "beliefs.json"


def _ensure_dir() -> None:
    beliefs_path().parent.mkdir(parents=True, exist_ok=True)


def load_beliefs() -> list[dict[str, Any]]:
    p = beliefs_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception:
        logger.exception("failed loading beliefs.json")
        return []


def save_beliefs(rows: list[dict[str, Any]]) -> None:
    _ensure_dir()
    p = beliefs_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    tmp.replace(p)


def _laplace_confidence(conf: int, refut: int) -> float:
    return round(float(conf) / float(conf + refut + 2), 4)


def _outcome_bucket(pnl: float, pnl_pct: float | None) -> str:
    eps = float(os.getenv("FORTRESS_BELIEF_FLAT_EPS_USD", "0.01") or "0.01")
    if abs(pnl) < eps and (pnl_pct is None or abs(float(pnl_pct)) < 1e-8):
        return "flat"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "flat"


def _opposite_outcome(bucket: str) -> str | None:
    if bucket == "win":
        return "loss"
    if bucket == "loss":
        return "win"
    return None


def _lesson_llm(
    *,
    regime: str,
    strategy: str,
    conf: float,
    outcome: str,
    pnl_pct: float,
) -> str:
    prompt = (
        "In one sentence, what trading lesson does this outcome teach? "
        f"Regime: {regime}, Strategy: {strategy}, Signal confidence: {conf:.2f}, "
        f"Outcome: {outcome}, P&L: {pnl_pct:.3f}%. Be specific and actionable."
    )

    @exponential_backoff_retry()
    def _call():
        from agents.unified_ai_agent import call_deepseek

        text, _ = call_deepseek(prompt, max_out_tokens=120)
        return (text or "").strip()

    try:
        s = _call()
        return s[:500] if s else ""
    except Exception:
        logger.exception("belief lesson LLM failed")
        return ""


def add_or_update_belief(
    *,
    symbol: str,
    regime_at_entry: str,
    strategy_used: str,
    entry_signal_confidence: float,
    pnl: float,
    pnl_pct: float | None,
    hold_duration_hours: float,
    slot_key: str | None = None,
    vix_regime: str | None = None,
) -> dict[str, Any]:
    """Insert or merge belief; refute opposite-outcome rows for same regime+strategy."""
    bucket = _outcome_bucket(float(pnl), pnl_pct)
    rows = load_beliefs()
    pct = float(pnl_pct or 0.0)
    lesson = _lesson_llm(
        regime=regime_at_entry,
        strategy=strategy_used,
        conf=float(entry_signal_confidence),
        outcome=bucket,
        pnl_pct=pct,
    )
    if not lesson.strip():
        lesson = f"{strategy_used} in {regime_at_entry} regime: {bucket} ({pct:+.1f}%)"

    match_idx = None
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        if (
            str(r.get("regime_at_entry")) == regime_at_entry
            and str(r.get("strategy_used")) == strategy_used
            and str(r.get("outcome")) == bucket
        ):
            match_idx = i
            break

    opp = _opposite_outcome(bucket)
    if opp:
        for j, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            if (
                str(r.get("regime_at_entry")) == regime_at_entry
                and str(r.get("strategy_used")) == strategy_used
                and str(r.get("outcome")) == opp
            ):
                cc = int(r.get("confirmation_count") or 1)
                rc = int(r.get("refutation_count") or 0) + 1
                r["refutation_count"] = rc
                r["confidence_score"] = _laplace_confidence(cc, rc)
                r["last_updated_at"] = datetime.now(timezone.utc).isoformat()
                rows[j] = r

    now = datetime.now(timezone.utc).isoformat()
    if match_idx is not None:
        r = rows[match_idx]
        cc = int(r.get("confirmation_count") or 0) + 1
        rc = int(r.get("refutation_count") or 0)
        r["confirmation_count"] = cc
        r["confidence_score"] = _laplace_confidence(cc, rc)
        r["last_updated_at"] = now
        rows[match_idx] = r
        save_beliefs(rows)
        return r

    row = {
        "belief_id": str(uuid.uuid4()),
        "created_at": now,
        "symbol": symbol.upper(),
        "regime_at_entry": regime_at_entry,
        "strategy_used": strategy_used,
        "entry_signal_confidence": float(entry_signal_confidence),
        "outcome": bucket,
        "pnl_pct": pct,
        "hold_duration_hours": float(hold_duration_hours),
        "pattern_description": lesson[:800],
        "confidence_score": _laplace_confidence(1, 0),
        "confirmation_count": 1,
        "refutation_count": 0,
        "last_updated_at": now,
    }
    if slot_key:
        row["slot_key"] = slot_key
    if vix_regime:
        row["vix_regime"] = vix_regime
    rows.append(row)
    save_beliefs(rows)
    return row


def get_top_beliefs(n: int = 10) -> list[dict[str, Any]]:
    rows = [r for r in load_beliefs() if isinstance(r, dict)]
    rows.sort(key=lambda x: float(x.get("confidence_score") or 0), reverse=True)
    return rows[: max(0, int(n))]


def _is_historical_seed(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "").strip() == "historical_seed"


def merge_domain_ingest_beliefs(
    records: list[dict[str, Any]] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Upsert domain-ingest macro/symbol signals into beliefs.json (source=domain_ingest)."""
    if records is None:
        from knowledge.domain_ingest_context import collect_valid_records

        records = collect_valid_records()
    skipped = 0
    conflicts = 0
    would_merge = 0
    rows = load_beliefs()
    merged = 0
    now = datetime.now(timezone.utc).isoformat()

    def slot_key(rec: dict[str, Any]) -> str:
        return "|".join(
            [
                "domain_ingest",
                str(rec.get("source") or ""),
                str(rec.get("signal_type") or ""),
                str(rec.get("ticker") or "MACRO"),
            ]
        )

    by_slot: dict[str, int] = {}
    for i, r in enumerate(rows):
        if isinstance(r, dict) and str(r.get("source") or "") == "domain_ingest":
            sk = str(r.get("slot_key") or "")
            if sk:
                by_slot[sk] = i

    for rec in records:
        if not isinstance(rec, dict):
            skipped += 1
            continue
        sk = slot_key(rec)
        conf = max(0.35, min(0.85, float(rec.get("confidence") or 0.5)))
        summary = json.dumps(rec.get("value"), default=str)[:400]
        pattern = (
            f"[{rec.get('source')}/{rec.get('signal_type')}] "
            f"{rec.get('ticker') or 'MACRO'}: {summary}"
        )
        row = {
            "belief_id": str(uuid.uuid4()),
            "created_at": now,
            "last_updated_at": now,
            "symbol": str(rec.get("ticker") or "MACRO").upper(),
            "regime_at_entry": "NEUTRAL_RANGING",
            "strategy_used": "mean_reversion",
            "entry_signal_confidence": conf,
            "outcome": "neutral",
            "pnl_pct": 0.0,
            "hold_duration_hours": 0.0,
            "pattern_description": pattern[:800],
            "confidence_score": conf,
            "confirmation_count": 1,
            "refutation_count": 0,
            "source": "domain_ingest",
            "slot_key": sk,
        }
        idx = by_slot.get(sk)
        if idx is not None:
            prev = rows[idx]
            row["belief_id"] = str(prev.get("belief_id") or row["belief_id"])
            row["created_at"] = str(prev.get("created_at") or now)
            row["confirmation_count"] = int(prev.get("confirmation_count") or 0) + 1
            rows[idx] = row
            conflicts += 1
        else:
            rows.append(row)
            by_slot[sk] = len(rows) - 1
        would_merge += 1
        merged += 1

    if merged and not dry_run:
        save_beliefs(rows)
    return {
        "merged_count": merged,
        "would_merge_count": would_merge,
        "skipped_count": skipped,
        "conflicts": conflicts,
        "dry_run": dry_run,
    }


def append_historical_seed_beliefs(
    records: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    max_confidence: float = 0.80,
    min_confidence: float = 0.55,
) -> tuple[int, list[str]]:
    """
    Merge pre-seeded belief rows (does not delete live-trade beliefs).
    Replaces prior historical_seed row with same (strategy_used, symbol, regime_at_entry).
    Confidence clamps use seed_tier (1/2/3) when present on each record.
    """
    try:
        from agents.historical_seeder.seed_tiers import tier_conf_bounds as _tier_bounds
    except ImportError:

        def _tier_bounds(_t: str) -> tuple[float, float]:
            return (min_confidence, max_confidence)

    skipped: list[str] = []
    rows = load_beliefs()
    added = 0

    def key_seed(r: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(r.get("strategy_used") or ""),
            str(r.get("symbol") or ""),
            str(r.get("regime_at_entry") or ""),
        )

    for rec in records:
        if not isinstance(rec, dict):
            skipped.append("non-dict record")
            continue
        st = str(rec.get("seed_tier") or "1").strip()
        lo, hi = _tier_bounds(st)
        conf = float(rec.get("confidence_score") or 0)
        conf = max(lo, min(hi, conf))
        rec["confidence_score"] = round(conf, 4)
        esc = float(rec.get("entry_signal_confidence") or conf)
        rec["entry_signal_confidence"] = round(max(lo, min(hi, esc)), 4)
        rec["source"] = "historical_seed"
        if "seed_tier" not in rec or not str(rec.get("seed_tier") or "").strip():
            rec["seed_tier"] = st
        k = key_seed(rec)
        idx = None
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            if not _is_historical_seed(row):
                continue
            if key_seed(row) == k:
                idx = i
                break
        if idx is not None:
            rec["belief_id"] = rows[idx].get("belief_id") or rec.get("belief_id")
            rows[idx] = rec
            added += 1
        else:
            if not rec.get("belief_id"):
                rec["belief_id"] = str(uuid.uuid4())
            rows.append(rec)
            added += 1

    if not dry_run:
        save_beliefs(rows)
    return added, skipped


def get_beliefs_for_context(regime: str, strategy: str, *, limit: int = 5) -> list[dict[str, Any]]:
    rows = [r for r in load_beliefs() if isinstance(r, dict)]
    rel = [
        r
        for r in rows
        if str(r.get("regime_at_entry")) == str(regime) and str(r.get("strategy_used")) == str(strategy)
    ]
    # Prefer live (non-seed) beliefs first at equal confidence; then higher confidence.
    def _ctx_rank(x: dict[str, Any]) -> tuple[int, int, float]:
        if not _is_historical_seed(x):
            return (0, 0, -float(x.get("confidence_score") or 0))
        try:
            tn = int(str(x.get("seed_tier") or "1").strip() or "1")
        except ValueError:
            tn = 9
        return (1, tn, -float(x.get("confidence_score") or 0))

    rel.sort(key=_ctx_rank)
    return rel[: max(0, int(limit))]


def get_beliefs_for_consciousness(consciousness: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """Beliefs ranked by regime + optional slot/vix tags from consciousness bundle."""
    rows = [r for r in load_beliefs() if isinstance(r, dict)]
    if not rows:
        return []
    temporal = consciousness.get("temporal") or {}
    slot_key = str(temporal.get("slot_key") or "")
    vix_reg = str(consciousness.get("vix_regime") or "")
    tape = consciousness.get("market_tape") or {}
    regime_hint = str(tape.get("tape_trend") or "mixed")

    def _score(b: dict[str, Any]) -> tuple[int, float]:
        s = 0
        if str(b.get("regime_at_entry") or "").lower() in regime_hint.lower():
            s += 2
        if slot_key and str(b.get("slot_key") or "") == slot_key:
            s += 3
        if vix_reg and str(b.get("vix_regime") or "") == vix_reg:
            s += 2
        if not _is_historical_seed(b):
            s += 1
        return (-s, -float(b.get("confidence_score") or 0))

    ranked = sorted(rows, key=_score)
    return ranked[: max(0, int(limit))]


def format_beliefs_for_consciousness_context(consciousness: dict[str, Any], *, limit: int = 5) -> str:
    if str(os.getenv("FORTRESS_BELIEF_INJECT", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return ""
    beliefs = get_beliefs_for_consciousness(consciousness, limit=limit)
    if not beliefs:
        return "LEARNED BELIEFS: None matched to current consciousness context."
    lines = []
    for b in beliefs:
        conf = float(b.get("confidence_score") or 0)
        desc = str(b.get("pattern_description") or "")[:200]
        slot = b.get("slot_key")
        tag = f" slot={slot}" if slot else ""
        lines.append(f"- [{conf:.2f}]{tag} {desc}")
    return "LEARNED BELIEFS (consciousness-matched):\n" + "\n".join(lines)


def format_beliefs_prompt_section(regime: str, strategy: str) -> str:
    if str(os.getenv("FORTRESS_BELIEF_INJECT", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return ""
    beliefs = get_beliefs_for_context(regime, strategy, limit=5)
    if not beliefs:
        return "LEARNED BELIEFS: None yet — building from live experience."
    lines = []
    for b in beliefs:
        conf = float(b.get("confidence_score") or 0)
        desc = str(b.get("pattern_description") or "")[:240]
        if _is_historical_seed(b):
            st = str(b.get("seed_tier") or "1").strip()
            tag = f"[SEEDED·T{st}] "
        else:
            tag = ""
        lines.append(f"- [{conf:.2f}] {tag}{desc}")
    return "LEARNED BELIEFS (from trade history, highest confidence first):\n" + "\n".join(lines)


def belief_dashboard_snapshot() -> dict[str, Any]:
    rows = [r for r in load_beliefs() if isinstance(r, dict)]
    top = sorted(rows, key=lambda x: float(x.get("confidence_score") or 0), reverse=True)[:5]
    recent = sorted(rows, key=lambda x: str(x.get("created_at") or ""), reverse=True)[:3]
    return {
        "total_beliefs": len(rows),
        "top_beliefs": top,
        "recent_beliefs": recent,
    }
