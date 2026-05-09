"""Orchestrate domain ingests; write ingest_health.json."""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("domain_ingest.runner")


def _load_dotenv(root: Path) -> None:
    """So FRED_API_KEY / ALPHA_VANTAGE_KEY in repo .env apply to CLI/cron ingest."""
    try:
        from utils.env_load import load_fortress_dotenv

        load_fortress_dotenv(root)
    except Exception:
        pass


def _root() -> Path:
    raw = (os.environ.get("FORTRESS_AI_PROJECT_ROOT") or "").strip()
    if raw:
        return Path(raw)
    # agents/domain_ingest/ingest_runner.py → repo root
    return Path(__file__).resolve().parent.parent.parent


def _build_runners(root: Path) -> list[tuple[str, Any]]:
    from agents.domain_ingest.cot_report_ingest import CotReportIngest
    from agents.domain_ingest.fred_ingest import FredIngest
    from agents.domain_ingest.news_sentiment_ingest import NewsSentimentIngest
    from agents.domain_ingest.sec_edgar_ingest import SecEdgarIngest

    return [
        ("sec_edgar", SecEdgarIngest(root)),
        ("fred", FredIngest(root)),
        ("news_sentiment", NewsSentimentIngest(root)),
        ("cot_report", CotReportIngest(root)),
    ]


def _run_ingest_subset(root: Path, names: set[str]) -> dict[str, Any]:
    _load_dotenv(root)
    ro = str(os.getenv("FORTRESS_INGEST_READ_ONLY", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if ro:
        logger.info("INGEST READ-ONLY MODE — data saved, not wired to decisions.")

    day_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    sources_out: dict[str, Any] = {}
    last_run = datetime.now(timezone.utc).isoformat()

    runners = [pair for pair in _build_runners(root) if pair[0] in names]
    unknown = names - {pair[0] for pair in _build_runners(root)}
    if unknown:
        raise ValueError(f"unknown --source name(s): {sorted(unknown)}")

    for name, ing in runners:
        status = "ok"
        err = None
        rec_count = 0
        last_success = None
        try:
            raw = ing.fetch()
            recs = ing.parse(raw)
            rec_count = len(recs)
            p = ing.save(recs, day_stamp=day_stamp, root=root)
            last_success = datetime.now(timezone.utc).isoformat()
            if p:
                logger.info("%s saved %s records -> %s", name, rec_count, p)
        except Exception as e:
            status = "error"
            err = f"{type(e).__name__}: {e}"
            logger.exception("%s ingest failed", name)

        sources_out[name] = {
            "status": status,
            "last_success": last_success,
            "record_count": rec_count,
            "error": err,
        }

    out_path = root / "data" / "domain_intelligence" / "ingest_health.json"
    prior: dict[str, Any] = {}
    if out_path.exists():
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            prior = {}
    prior_sources = prior.get("sources")
    merged_sources: dict[str, Any] = prior_sources if isinstance(prior_sources, dict) else {}
    merged_sources.update(sources_out)

    health: dict[str, Any] = {
        "last_run": last_run,
        "read_only_mode": ro,
        "sources": merged_sources,
    }
    if len(names) < 4:
        health["last_subset"] = sorted(names)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(health, indent=2), encoding="utf-8")
    return health


def run_all_sources() -> dict[str, Any]:
    root = _root()
    names = {pair[0] for pair in _build_runners(root)}
    return _run_ingest_subset(root, names)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Domain ingest orchestrator")
    ap.add_argument(
        "--source",
        action="append",
        metavar="NAME",
        help="Run a single source (sec_edgar, fred, news_sentiment, cot_report). Repeatable.",
    )
    args = ap.parse_args()
    root = _root()
    if args.source:
        subset = {str(s).strip().lower() for s in args.source if str(s).strip()}
        out = _run_ingest_subset(root, subset)
    else:
        out = run_all_sources()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
