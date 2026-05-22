# Intentional Classic vs AI comparison

## Goal

Compare **realized P&L** and trade cadence between two isolated stacks on the same VM, not a single blended account.

| Stack | Repo / service | Account | Role |
|-------|----------------|---------|------|
| **Classic** | `trading-bot` | Alpaca paper (Classic keys) | Rule-based screener + orchestrator entry window |
| **Fortress AI** | `fortress-ai` | Separate Alpaca paper (AI keys) | Unified agent + optional SPY intraday |
| **SPY intraday** | `fortress-ai` (`fortress-ai-spy-agent`) | Same or dedicated AI paper | Index ladder skims |

## Recommended A/B setup

1. **Separate Alpaca paper accounts** (or sub-accounts) with distinct API keys in each repo’s `.env`.
2. **Classic**: production cron (`deploy/cron/trading-bot.production.crontab`) — screen 14:35 / 15:05 ET, monitor every 5 min.
3. **AI unified**: `FORTRESS_AI_DRY_RUN=0`, `FORTRESS_AI_MANUAL_ONLY=0` for RTH auto cycles; tune `FORTRESS_AI_MIN_CONFIDENCE`.
4. **SPY** (optional): `FORTRESS_SPY_DRY_RUN=0`; enable SI only after baseline fills (`FORTRESS_SPY_SI_ENABLED=1`).
5. **Dashboard**: Fortress AI Command Center → Compare drawer; realized P&L from Classic `pnl_ledger` and AI `data/pnl_ledger.jsonl`.

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
3. AI `confidence_below_threshold` share — tune prompt or `FORTRESS_*_MIN_CONFIDENCE`.
4. Decide whether to enable SPY SI or unified SI for the next week.
