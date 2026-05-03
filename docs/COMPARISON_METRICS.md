# Comparison metrics

Track these for **Classic** vs **Fortress AI**:

| Metric | Classic source (typical) | Fortress AI source |
|--------|--------------------------|---------------------|
| Opportunity detection rate | Screening / entry funnel stats, `daily_signals`, dashboard | `ai_metrics.jsonl` — `opportunity_detection` fraction |
| Win rate | Closed trades / `decisions_log.jsonl` / P&amp;L ledger | Paper fills once enabled + outcomes logged |
| P&amp;L per trade | Ledger / Alpaca history | Same, isolated account |
| Decision latency | Orchestrator timings | `decision_latency_ms`, `total_cycle_latency_ms` in `ai_metrics.jsonl` |
| API cost | `api_costs.jsonl` (Classic) | `ai_llm_cost_ledger.jsonl` + weekly rollup |

Use:

```bash
export CLASSIC_DATA_DIR=/path/to/trading-bot/data
export FORTRESS_AI_DATA_DIR=/path/to/fortress-ai/data
PYTHONPATH=. python3 scripts/compare_systems.py
```

## Fallback (4-week review)

1. **AI underperforms** → operate Classic only; keep Fortress AI in dry-run or powered down.
2. **AI outperforms on risk-adjusted metrics** → consider hybrid (e.g., AI proposes, Classic gates execute).
3. **Tie** → compare by regime, latency, and cost; split responsibilities accordingly.
