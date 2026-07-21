# Portfolio session entry guards

Macro guards that block new swarm entries when portfolio session conditions
are unfavorable relative to the market benchmark (SPY).

## market_relative_underperformance

Blocks new entries when session alpha vs SPY (portfolio return minus SPY return)
falls below a configurable threshold within a rolling window.

### Configuration

Primary config: `config/portfolio_session.yaml` (also merged from
`utils/portfolio_session/config/guards.yaml`, `config/guards.yaml`, and
`utils/portfolio_session/config/guard_config.yaml`).

```yaml
market_relative_underperformance:
  enabled: true
  market_relative_underperformance_threshold: -0.5  # percent points; block when alpha vs SPY below
  underperformance_threshold_pct: -0.5  # block when alpha vs SPY < -0.5%
  lookback_period: '1d'
  threshold_pct: -0.5   # legacy alias
  window_seconds: 300   # rolling 5-minute window for alpha computation
  cooldown_seconds: 3600
```

Environment overrides:

- `MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD` — threshold in percent points (default `-0.5`)
- `MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_PCT` — alias for the above
- `FORTRESS_MARKET_RELATIVE_UNDERPERFORMANCE_THRESHOLD_PCT` — fortress-specific alias
- `FORTRESS_MARKET_RELATIVE_GATE_*` — see `risk_manager.py` for cooldown/window tuning

### Behavior

1. On each flat-side entry attempt, `EntryGate` evaluates
   `MarketRelativeUnderperformanceGuard` first among portfolio session macro guards.
2. Session alpha vs SPY is computed from `alpha_snapshots` within `window_seconds`,
   or from `alpha_vs_spy_pct` / `session_alpha_vs_spy` when snapshots are unavailable.
3. When alpha &lt; `threshold_pct`, the guard blocks with
   `reason=market_relative_underperformance` and logs
   `market_relative_underperformance MarketRelativeGate` plus
   `entry_blocked_by_market_relative`.
4. Per-symbol gates (denylist, pause_entries, pattern_disables, causation, edge)
   run before the macro market-relative block in swarm signal paths
   (`swarm_gate_order_specific_before_macro`).

### Integration

- `utils/portfolio_session/entry_guards.py` — `market_relative_underperformance_gate()` + `evaluate_entry_blocks()`
- `utils/portfolio_session/session_manager.py` — `_evaluate_entry_guards()` + breakdown merge
- `utils/portfolio_session/entry_gate.py` — guard pipeline
- `utils/portfolio_session/risk_manager.py` — cooldown + `entry_blocked_by_market_relative()`
- `agents/skim_swarm/signal.py`, `agents/infra_swarm/signal.py` — swarm wiring
- `risk/guard_engine.py` — programmatic entry guard evaluation

### Detectable markers

| Marker | Purpose |
|--------|---------|
| `market_relative_underperformance` | block_reason / guard name |
| `MarketRelativeGate` | log prefix for guard activation |
| `entry_blocked_by_market_relative` | info log when entry blocked |
| `swarm_gate_order_specific_before_macro` | per-symbol gates precede macro block |
| `market_relative_underperformance_threshold` | config / log detail for active threshold |
| `market_relative_underperformance_threshold_bps` | threshold in basis points in logs |
| `session_underperforming` | detail when alpha below threshold |
| `entry_block_breakdown` | per-block counters including `market_relative` |
