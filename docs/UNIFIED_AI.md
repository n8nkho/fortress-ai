# Unified AI agent

LLM-centric discretionary sleeve (`fortress-ai-agent`) — **research / off-universe names**, not the primary intraday execution path.

## Role in the multi-stack layout

| Stack | Cadence | Role |
|-------|---------|------|
| **Skim swarm** | Always-on RTH | Core intraday execution (mega-cap skims) |
| **Infra swarm** | Always-on RTH | Capped AI-infra thematic overlay |
| **Unified AI** | 5 min RTH loop | High-confidence discretionary on **off-denylist** symbols |
| **Classic** (sibling repo) | 2× daily screen | Mean-reversion swing book |

Skim and infra own NVDA, SPY, AAPL, MSFT, etc. Unified must **not** compete for those symbols.

## Symbol partition

- **`FORTRESS_AI_SYMBOL_DENYLIST`** — skim universe + infra names; unified `enter_position` blocks with `skim_swarm_reserved`.
- **`FORTRESS_AI_ELIGIBLE_UNIVERSE`** — default pool unified may enter: `QQQ,IWM,DIA,JPM,XOM,UNH,COST,WMT,DIS,BA`.
- **`utils/unified_symbol_pool.py`** — filters Classic screener candidates off the denylist; feeds `watchlist_hint` in `observe()`.

If the LLM proposes NVDA/SPY/AAPL while they are denylisted, act blocks — this is expected.

## Confidence gate

- **`FORTRESS_AI_MIN_CONFIDENCE`** — env floor (recommended **0.85–0.92** for paper).
- **`get_confidence_threshold()`** — never returns below the env floor even if `data/tunable_params_overrides.json` has a lower value.
- **`FORTRESS_AI_CONFIDENCE_FLOOR_LOCK=1`** — SI governance will **not** auto-lower confidence (`low_unified_execution_rate` is monitor-only).

Low execution rate with many `skim_swarm_reserved` blocks is a **partition success**, not a tuning failure — widen `FORTRESS_AI_ELIGIBLE_UNIVERSE`, not lower confidence.

## Other guards

- **`utils/unified_enter_guard.py`** — enter cooldown when flat but recent enter (duplicate-entry prevention).
- **`pre_trade_gate`** — same submission rails as Classic.
- Weekly LLM cap: `FORTRESS_AI_WEEKLY_COST_CAP_USD`.

## Operations

```bash
journalctl -u fortress-ai-agent -f
PYTHONPATH=. python3 agents/unified_ai_agent.py --dry-run --once
```

State: `data/ai_decisions.jsonl`, `data/ai_state.json`, `data/tunable_params_overrides.json`.

API: `GET /api/ai/current_state`, diagnostics via `GET /api/trading/diagnostics`.
