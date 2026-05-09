"""CLI: download → features → patterns → beliefs (+ regime playbooks + ingest health bump)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from agents.historical_seeder import belief_seeder
from agents.historical_seeder.feature_engine import run_features
from agents.historical_seeder.paths import ingest_health_path, pattern_results_path, repo_root
from agents.historical_seeder.pattern_miner import mine_all
from agents.historical_seeder.price_loader import run_downloads
from agents.historical_seeder.regime_knowledge import (
    build_playbook_summaries,
    mine_regime_stats,
    regime_transition_beliefs,
)
from agents.historical_seeder.seed_tiers import summarize_tiers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("historical_seeder.run_seeder")


def _merge_ingest_health_historical_seed(record_count: int, *, error: str | None = None) -> None:
    root = repo_root()
    path = root / "data" / "domain_intelligence" / "ingest_health.json"
    prior: dict[str, Any] = {}
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            prior = {}
    sources = prior.get("sources")
    merged_sources: dict[str, Any] = sources if isinstance(sources, dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    merged_sources["historical_seed"] = {
        "status": "error" if error else "ok",
        "last_success": None if error else now,
        "record_count": int(record_count),
        "date_range": "2000-2026",
        "error": error,
    }
    out = {
        "last_run": prior.get("last_run") or now,
        "read_only_mode": prior.get("read_only_mode", True),
        "sources": merged_sources,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    logger.info("updated ingest_health historical_seed record_count=%s", record_count)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Historical belief pre-seeding pipeline")
    ap.add_argument("--dry-run", action="store_true", help="Do not write beliefs.json or ingest_health")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--skip-features", action="store_true")
    ap.add_argument("--skip-patterns", action="store_true")
    args = ap.parse_args(argv)

    root = repo_root()
    logger.info("repo root %s", root)

    if not args.skip_download:
        logger.info("step 1: price download")
        run_downloads(skip_if_today=True)
    else:
        logger.info("skip download")

    if not args.skip_features:
        logger.info("step 2: features")
        fe = run_features()
        if fe.get("error"):
            logger.error("features failed: %s", fe)
            return 1
    else:
        logger.info("skip features")

    if not args.skip_patterns:
        logger.info("step 3: pattern mining")
        mine_all()
    else:
        logger.info("skip patterns")

    pr_path = pattern_results_path()
    if not pr_path.exists():
        logger.error("missing %s", pr_path)
        return 1
    pattern_results = json.loads(pr_path.read_text(encoding="utf-8"))

    logger.info("step 4: belief rows from patterns")
    belief_rows, rejected = belief_seeder.patterns_to_belief_rows(pattern_results)

    logger.info("step 5: regime stats + playbooks")
    stats = mine_regime_stats()
    if not args.dry_run:
        rs_path = pattern_results_path().parent / "regime_stats.json"
        rs_path.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")

    playbooks = build_playbook_summaries(pattern_results)
    belief_rows.extend(belief_seeder.playbook_belief_rows(playbooks))

    trans = regime_transition_beliefs(stats)
    belief_rows.extend(belief_seeder.regime_meta_beliefs(trans))

    logger.info("step 6: persist beliefs count=%s dry_run=%s", len(belief_rows), args.dry_run)
    from utils.belief_manager import append_historical_seed_beliefs

    added, skipped = append_historical_seed_beliefs(belief_rows, dry_run=args.dry_run)

    rec_err = None
    try:
        if not args.dry_run:
            _merge_ingest_health_historical_seed(added, error=None)
        else:
            logger.info("dry-run: skip ingest_health merge")
    except Exception as e:
        rec_err = str(e)
        logger.exception("ingest health merge failed")

    tier_counts = summarize_tiers(belief_rows)
    print("\n=== Historical seed summary ===")
    print(f"Belief rows prepared: {len(belief_rows)}")
    print(
        f"By tier: T1={tier_counts.get('1', 0)} strong | "
        f"T2={tier_counts.get('2', 0)} hypothesis | T3={tier_counts.get('3', 0)} exploratory"
    )
    print(f"Merged into beliefs.json: {added} (dry_run={args.dry_run})")
    if skipped:
        print(f"Skipped records: {skipped}")
    print("\nRejected / deferred:")
    for r in rejected[:40]:
        print(f"  - {r}")
    if len(rejected) > 40:
        print(f"  ... and {len(rejected) - 40} more")
    if rec_err:
        print(f"Ingest health error: {rec_err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
