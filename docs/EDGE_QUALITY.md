# Edge quality (skim + infra swarms)

Shared admission and exit-quality layer for intraday swarms — no LLM.

## Components

| Module | Role |
|--------|------|
| `utils/edge_quality.py` | RR / cost / expectancy math; `time_stop`; `bracket_prices` + `clamp_bracket_prices` |
| `utils/edge_quality_config.py` | Env flags (`FORTRESS_EDGE_*`) |
| `utils/edge_scorecard.py` | Session scorecard from exits (payoff ratio, profit factor, by pattern/symbol) |
| `utils/edge_autofix.py` | RTH autofix: tighten stops, boost RR margin, disable toxic patterns |
| `utils/alpaca_execution.py` | Passive limits + broker bracket OCO on entry |

## Entry gates (when `FORTRESS_EDGE_QUALITY=1`)

1. **RR gate** — `target_usd / stop_usd` must clear breakeven payoff for estimated win rate × safety margin.
2. **Cost gate** — target must cover round-trip spread + slippage + fees.
3. **Expectancy gate** — pattern-level session expectancy must exceed floor (after min samples).

Blocked entries log `edge_rr_gate`, `edge_cost_gate`, or `edge_expectancy_gate` in act detail.

## Bracket exits

When `FORTRESS_EDGE_BRACKET=1` (default with edge quality on), entries submit Alpaca **bracket** orders with take-profit and stop-loss.

Alpaca requires stop/take-profit to be at least **$0.01** away from the base (limit or market) price. `clamp_bracket_prices()` enforces this using the **actual passive limit price** when `FORTRESS_EDGE_PASSIVE_ENTRY=1`, avoiding 422 errors like `stop_loss.stop_price must be <= base_price - 0.01`.

On bracket failure, execution falls back to market (`order_type: market_fallback` in act detail).

## Session scorecard

Written to `data/{skim,infra}_swarm/edge_scorecard.json` during RTH. Key fields:

- `payoff_ratio`, `profit_factor`, `expectancy_usd`
- `by_pattern`, `by_symbol`, `by_exit_reason`

Used by `utils/rth_autonomous_si.py` and `utils/edge_autofix.py` for intraday tightening.

## Env (common)

```bash
FORTRESS_EDGE_QUALITY=1
FORTRESS_RR_GATE=1
FORTRESS_COST_GATE=1
FORTRESS_EXPECTANCY_GATE=1
FORTRESS_EDGE_BRACKET=1
FORTRESS_EDGE_PASSIVE_ENTRY=1
FORTRESS_TIME_STOP=1
```

See `.env.example` for full list.

## SI registry

Fix codes: `edge_rr_cost_gates`, `broker_bracket_exits`, `swarm_inverted_payoff`, `alpaca_bracket_tick_violation` in `config/si_fix_registry.json`.

Session post-mortems: `config/session_learnings_YYYYMMDD.json` (e.g. `session_learnings_20260601.json`).
