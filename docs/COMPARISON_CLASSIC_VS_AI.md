# Intentional Classic vs AI comparison

## Goal

Compare **realized P&L** and trade cadence between two isolated stacks on the same VM, not a single blended account.

| Stack | Repo / service | Account | Role |
|-------|----------------|---------|------|
| **Classic** | `trading-bot` | Alpaca paper (Classic keys) | Mean-reversion swing — 2× daily screen + monitor |
| **Skim swarm** | `fortress-ai` (`fortress-ai-skim-swarm`) | Fortress AI paper | Primary intraday rule-based skims |
| **Infra swarm** | `fortress-ai` (`fortress-ai-infra-swarm`) | Same Fortress paper | Capped AI-infra overlay (SRP) |
| **Unified AI** | `fortress-ai` (`fortress-ai-agent`) | Same Fortress paper | LLM discretionary on **off-denylist** names only |
| **SPY intraday** | `fortress-ai` (`fortress-ai-spy-agent`) | Same or dedicated paper | Optional; disable when skim is primary |

See also: [SKIM_SWARM.md](SKIM_SWARM.md), [INFRA_SWARM.md](INFRA_SWARM.md), [UNIFIED_AI.md](UNIFIED_AI.md).

## Recommended A/B setup

1. **Separate Alpaca paper accounts** (or sub-accounts) with distinct API keys in each repo’s `.env`.
2. **Classic**: production cron (`deploy/cron/trading-bot.production.crontab`) — screen 14:35 / 15:05 ET, monitor every 5 min.
3. **Skim + infra**: `FORTRESS_SKIM_DRY_RUN=0`, `FORTRESS_INFRA_DRY_RUN=0`; enable systemd units (see [deploy/README.md](../deploy/README.md)).
4. **Unified AI**: `FORTRESS_AI_DRY_RUN=0`, `FORTRESS_AI_MANUAL_ONLY=0`, `FORTRESS_AI_MIN_CONFIDENCE=0.88`, `FORTRESS_AI_CONFIDENCE_FLOOR_LOCK=1`, `FORTRESS_AI_ELIGIBLE_UNIVERSE=QQQ,IWM,...`.
5. **SPY** (optional): `FORTRESS_SPY_DRY_RUN=0`; disable when skim swarm is primary.
6. **Dashboard**: Fortress AI Command Center → Compare drawer; Classic `pnl_ledger` vs Fortress `data/pnl_ledger.jsonl` + swarm scorecards.

## Metrics to compare (14-day)

- Realized P&L (ledger)
- Trade count / execution rate
- Block reason distribution (`/api/trading/diagnostics`)
- LLM cost vs cap (`weekly_budget` in current state)

## What not to compare

- Raw “wait” counts without block reasons (AI may wait while Classic simply has no screen slot).
- Unrealized marks only — use closed-trade ledger for apples-to-apples.

## Weekly review checklist

1. Both ledgers non-empty or documented why (dry_run, entry window, confidence).
2. Cron heartbeats green in Classic dashboard during RTH.
3. AI `confidence_below_threshold` vs `skim_swarm_reserved` — latter means partition working; tune `FORTRESS_AI_ELIGIBLE_UNIVERSE`, not confidence down.
4. Skim/infra `edge_scorecard.json` payoff ratio and session_policy mode (critical = entries paused).
5. Review `config/session_learnings_*.json` and `GET /api/si/recommendations` for queued fixes.
