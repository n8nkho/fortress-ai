"""
Load recent domain intelligence records for AI Mind prompt (gated by FORTRESS_INGEST_READ_ONLY).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("domain_ingest_context")


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent


def _load_watchlist_tickers() -> set[str]:
    p = _root() / "data" / "watchlist.json"
    if not p.exists():
        return {"SPY", "QQQ", "AAPL"}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            return {str(x).strip().upper() for x in doc if str(x).strip()}
        if isinstance(doc, dict) and isinstance(doc.get("tickers"), list):
            return {str(x).strip().upper() for x in doc["tickers"] if str(x).strip()}
    except Exception:
        logger.warning("watchlist.json unreadable; using defaults")
    return {"SPY", "QQQ", "AAPL"}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def collect_valid_records(*, max_per_source: int = 8) -> list[dict[str, Any]]:
    base = _root() / "data" / "domain_intelligence"
    if not base.exists():
        return []
    now = datetime.now(timezone.utc)
    wl = _load_watchlist_tickers()
    out: list[dict[str, Any]] = []
    for src_dir in sorted(base.iterdir()):
        if not src_dir.is_dir() or src_dir.name.startswith("."):
            continue
        for fp in sorted(src_dir.glob("*.json"))[-3:]:
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                rows = raw if isinstance(raw, list) else raw.get("records", [])
                if not isinstance(rows, list):
                    continue
                for rec in rows:
                    if not isinstance(rec, dict):
                        continue
                    vu = _parse_iso(rec.get("valid_until"))
                    if vu is not None and vu < now:
                        continue
                    t = rec.get("ticker")
                    if t is not None and str(t).upper() not in wl and rec.get("signal_type") != "macro":
                        continue
                    out.append(rec)
            except Exception:
                logger.exception("failed reading ingest file %s", fp)
    out.sort(key=lambda r: str(r.get("ingested_at") or ""), reverse=True)
    return out[: max_per_source * 6]


def format_domain_ingest_prompt_section(observation: dict[str, Any]) -> str:
    if str(os.getenv("FORTRESS_INGEST_READ_ONLY", "1")).strip().lower() not in {"0", "false", "no", "off"}:
        return ""
    recs = collect_valid_records()
    if not recs:
        return (
            "DOMAIN INTELLIGENCE (non-price signals): No recent ingest records. "
            "These are data signals, not trade recommendations. Weigh appropriately."
        )
    lines = []
    for r in recs[:12]:
        st = str(r.get("signal_type") or "")
        src = str(r.get("source") or "")
        ticker = r.get("ticker")
        conf = float(r.get("confidence") or 0.0)
        summary = json.dumps(r.get("value"), default=str)[:220]
        tag = str(ticker or "MACRO")
        lines.append(f"- [{st.upper()}|{src}] {tag} conf={conf:.2f} — {summary}")
    body = "\n".join(lines)
    return (
        "DOMAIN INTELLIGENCE (non-price signals, read from ingest pipeline):\n"
        + body
        + "\nThese are data signals, not trade recommendations. Weigh appropriately."
    )
