# Fortress AI — System & Dashboard Whitepaper

**Document purpose:** External technical review of the Fortress AI platform, its operational architecture, and every dashboard panel with metric definitions, data provenance, and trading relevance.

**Version:** Neural Ops UI v2 (May 2026)  
**Primary dashboard:** Flask app at port 8050 (`FORTRESS_AI_DASHBOARD_PORT`)  
**Disclaimer:** Research and paper-trading infrastructure. Not investment advice.

---

## 1. Executive summary

Fortress AI is a multi-agent quantitative trading platform built around three distinct execution paths:

| Layer | Agent | Role | LLM? |
|-------|-------|------|------|
| Intraday skim | Skim Swarm | Always-on 1-minute rule-based long/short skims across 16 symbols | No |
| Strategic AI | Unified AI Agent | Periodic LLM-driven portfolio decisions on non-skim universe | Yes (DeepSeek) |
| Legacy SPY | SPY Intraday Agent | Ladder-based SPY session trading (optional; disabled when skim is primary) | No |

The **Command Center dashboard** is the single operational surface for monitoring all three paths, diagnosing why trades did or did not occur, tracking P&L, governing self-improvement, and comparing Fortress AI against a parallel **Classic** rules-based stack (trading-bot).

**Design principles:**

- Per-symbol independence — each skim ticker owns its parameters, pattern disables, and learning state.
- Deterministic hot-path adaptation — live recursive improvement without LLM latency in the skim loop.
- Tiered self-improvement — parameter tuning (Tier 0–1), prompt evolution (Tier 2), immutable risk rails (Tier 3).
- Full audit trail — every decision, block reason, cost, and governance action is JSONL-logged.

---

## 2. System architecture

### 2.1 Data flow

External data (Alpaca, yfinance/IEX, SEC EDGAR, FRED, news sentiment, COT) feeds three agents: Skim Swarm, Unified AI, and Domain Ingest. Agents write to JSONL/JSON stores under `data/`. The Flask dashboard (`dashboard/ai_command_center.py`) aggregates state and serves the Neural Ops UI v2.

### 2.2 Refresh tiers

| Tier | Interval | Mechanism | Panels affected |
|------|----------|-----------|-----------------|
| SSE | ~5s | GET /api/stream/decisions | Header status, AI Mind, positions |
| Fast | 60s + SSE | GET /api/ai/current_state | Market pulse, portfolio, beliefs |
| Medium | 60s | Partial state patch | Domain intel, screener, LLM spend |
| Slow | 300s | Ingest, SI, governance APIs | Ingest health, self-improvement, prompt evolution |
| Skim | 45s | GET /api/skim/status | Skim swarm panel |

Server-side cache: `build_current_state()` TTL ~25s.

---

## 3. Skim Swarm — core intraday engine

### 3.1 Universe (default 16 symbols)

SPY, NVDA, MSFT, GOOG, AMZN, AAPL, SOXX, NASA, BRK.B, AGIX, AVGO, LLY, V, MA, PLTR, CRWD

Each symbol is an independent agent with:

- **4 patterns:** rip_fade (short), pullback_uptrend (long), momentum_long, momentum_short
- **Per-symbol params:** entry deltas, target multiplier, disable_patterns, side pauses, causation blocks
- **Dual-timescale learning:** 10-year daily historical seeds + live 1-minute session stats
- **Phase 1 goal:** ≥75% of enabled patterns with positive net PnL (not trade win rate)
- **Phase 2 (deferred):** Karpathy-style autoresearch after enough symbols pass Phase 1 on live paper

### 3.2 Recursive self-improvement loop

Every 10 exits (then every 5), per symbol:

1. Disable PnL-negative patterns
2. Pause toxic sides (pause_long / pause_short)
3. Full symbol pause if session bleeds (pause_entries)
4. Tighten entry thresholds via causation keys (pattern|side|spy_regime|sym_regime|score_bucket)
5. Boot refresh restores historical seeds; live cannot undo seeds without strong recovery evidence

---

## 4. Dashboard panels — detailed reference

### Panel 0: Build stamp banner

**Purpose:** Deployment verification — confirms the browser loaded the expected UI bundle.

| Metric | Source | Why it matters |
|--------|--------|----------------|
| ui_build | Env FORTRESS_AI_DASHBOARD_BUILD | Prevents reviewing stale UI after deploy |

---

### Panel 1: Header bar

**Purpose:** Operational command center — instance identity, neural state, portfolio headline, operator controls.

| Metric | Field | Source | Trading importance |
|--------|-------|--------|-------------------|
| Instance name | state.instance | FORTRESS_INSTANCE_NAME | Multi-VM identification |
| Mode badge | state.dry_run | FORTRESS_AI_DRY_RUN | DRY-RUN = no live orders |
| Skim universe count | skim_preview.universe.length | FORTRESS_SKIM_UNIVERSE | Active basket size |
| Neural state | state.ui_status | _infer_ui_status() | WAITING → OBSERVING → THINKING → EXECUTING |
| Portfolio equity | state.portfolio.equity | Alpaca get_account() | Primary capital metric |
| API spend today | state.today_llm_spend_usd | ai_llm_cost_ledger.jsonl | LLM cost control |
| Halt button | POST /api/operator/halt | operator_trading_halt.json | Emergency kill switch |
| Run AI cycle | POST /api/agent/run-cycle | On-demand unified agent | Manual trigger |

**Neural state values:** EXECUTING (recent fill), THINKING (decision pending), OBSERVING (alive, idle), WAITING (off-hours/manual).

---

### Panel 2: Skim Swarm (live)

**API:** GET /api/skim/status (45s poll)  
**Purpose:** Real-time view of the always-on intraday skim basket — primary production path.

#### Summary tiles

| Metric | Source | Computation | Trading importance |
|--------|--------|-------------|-------------------|
| Daily realized | decisions.jsonl | Sum executed exit PnL in ET session | Today's closed P&L |
| Daily unrealized | Alpaca positions | Open position mark-to-market | Intraday drift |
| Session net | Computed | realized + unrealized | Total session P&L |
| Exit count | decisions.jsonl | Exit events today | Adaptation sample size |
| Open positions | Alpaca + state | Positions in universe | vs max_open (default 6) |
| HALTED | swarm_state.json | Daily stop gate (-$200 default) | Risk circuit breaker |

#### Per-symbol table

| Column | Source | Trading importance |
|--------|--------|-------------------|
| Side | Alpaca / agent state | Directional exposure |
| Price / Chg % | 1m IEX/Alpaca bars | Intraday momentum |
| Unrealized / Realized | Alpaca + learned/*.json | Per-symbol P&L |
| Wins / Losses / Exits | learned/*.json | Adaptation input |
| Last action | state/{sym}.json | Latest agent decision |

---

### Panel 3: Why no trade?

**API:** trading_diagnostics in current_state (14-day lookback)  
**Purpose:** Diagnostic — why blocked/skipped trades occurred.

**Skim column key metrics:** waves, entry proposed/executed/rate, HALTED, day P&L, **block_reason_counts** (pattern_disabled, pause_entries, spread_too_wide, causation_blocked, cooldown, no_edge, eod_force_flatten).

**Fortress AI column:** dry_run, min_confidence, executed/cycles, block_reason_counts from ai_decisions.jsonl.

**Computation:** utils/trading_diagnostics.py normalizes reasoning strings into categorical buckets.

---

### Panel 4: AI Mind (column 1)

**Purpose:** Unified LLM Agent reasoning, confidence, beliefs.

| Section | Source | Trading importance |
|---------|--------|-------------------|
| Market assessment | ai_decisions.jsonl | LLM regime read |
| Chain of thought | Same | Audit trail |
| Confidence | 0–1 score | vs min_confidence gate |
| Trade belief memory (P7) | beliefs/beliefs.json | Persistent cross-session learning |
| Recent decisions (5) | decisions tail | Quick history |

**Note:** Unified AI does not trade skim-universe symbols (FORTRESS_AI_SYMBOL_DENYLIST).

---

### Panel 5: Market pulse / Active scan / Positions (column 2)

| Section | Key metrics | Source |
|---------|-------------|--------|
| Market pulse | SPY price/change, VIX, RSI(14) | yfinance, charts API |
| Active scan | Symbol chips, screener source | AI screen_market or Classic fallback |
| Positions | Sym, Qty, Entry, Last, P&L | Alpaca get_all_positions() |

---

### Panel 6: Performance / API usage / System / Domain intel / Ingest (column 3)

| Section | Key metrics | Source |
|---------|-------------|--------|
| Performance | LLM spend chart (14d) | ai_llm_cost_ledger.jsonl |
| API usage | Today/week spend, cap bar, calls | api_costs.py, weekly cap env |
| System checklist | Kill switch, Alpaca, SSE, cap | operator halt, portfolio |
| Domain intel | Regime/strategy hints, concept count | knowledge/intel.py, ingest |
| Ingest health | SEC/FRED/News/COT status | ingest_health.json |

---

### Panel 7: Expert mode (overlay)

**Trigger:** Header Expert or key E | **API:** GET /api/expert/bundle

Tabs: Prompt note, last decision JSON, cost ledger tail, system logs, raw state JSON.

---

### Panel 8: Self-improvement (Tier 0–1)

**APIs:** /api/self_improvement/* | **Engine:** self_improvement_engine.py

| Parameter | Bounds | Effect |
|-----------|--------|--------|
| confidence_threshold | 0.6–0.95 | LLM entry gate |
| decision_interval | 120–1800s | Loop sleep |
| rsi_entry_threshold | 35–50 | RSI entry hint |

Flow: Propose → Shadow → Approve/Reject → Monitor → Auto-revert if win rate < 0.75. Velocity: max 1 change/7d, 3/31d.

---

### Panel 9: Prompt evolution (Tier 2)

**APIs:** /api/prompt_evolution/* | Additive appendix only — cannot modify schema or risk params.

Metrics: active appendix, A/B test status, pending proposal, event log.

---

### Panel 10: Governance (Tiers 0–3)

| Tier | Risk | Behavior |
|------|------|----------|
| 0 | Low | Auto-approve if shadow criteria met |
| 1 | Medium | 24h human veto window |
| 2 | Prompt | Human approval via prompt evolution |
| 3 | Immutable | Blocked — position size, stops, exposure |

---

### Panel 11: Classic vs AI comparison drawer

**API:** GET /api/comparison | Side-by-side Fortress AI vs Classic (trading-bot) on separate Alpaca accounts.

Metrics: equity, realized/unrealized P&L, win rate, trades/cycles today, opportunity flags, symbol overlap.

---

### Panel 12: SPY Intraday (not mounted in live UI)

Template and APIs exist; typically disabled when skim swarm is primary.

---

## 5. Data architecture

| Path | Written by | Read by |
|------|-----------|---------|
| data/skim_swarm/decisions.jsonl | Skim agent | Skim panel, diagnostics |
| data/skim_swarm/learned/*.json | Symbol learning | Per-symbol stats |
| data/ai_decisions.jsonl | Unified AI | AI Mind |
| data/ai_llm_cost_ledger.jsonl | API costs | API usage |
| data/beliefs/beliefs.json | Belief manager | Trade belief memory |
| data/operator_trading_halt.json | Operator | Kill switch |

---

## 6. Security and risk controls

| Control | Mechanism |
|---------|-----------|
| Kill switch | operator_trading_halt.json |
| Daily stop | FORTRESS_SKIM_DAILY_STOP_USD (-$200) |
| Max open positions | Default 6 |
| Spread filter | FORTRESS_SKIM_MAX_SPREAD_BPS (25) |
| EOD flatten | Force flat before close |
| Weekly LLM cap | FORTRESS_AI_WEEKLY_COST_CAP_USD |
| Symbol denylist | Unified AI blocked from skim universe |

---

## 7. Known limitations

1. No billing module — LLM cost tracking only.
2. Win rate placeholder until sufficient closed trades.
3. SPY panel unwired when skim is primary.
4. Historical verify uses daily bars; live skim uses 1-minute bars.
5. Paper trading on Alpaca paper accounts.

---

## 8. Roadmap

| Phase | Status | Goal |
|-------|--------|------|
| Phase 1 | Active | ≥75% winning-pattern share per symbol |
| Phase 2 | Deferred | Autoresearch after FORTRESS_SKIM_AUTORESEARCH_MIN_WINNING_SYMBOLS gate |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| Pattern | rip_fade, pullback_uptrend, momentum_long, momentum_short |
| Winning pattern | Positive net PnL after min samples — not trade win rate |
| Wave | One skim loop iteration across universe |
| Causation key | Context bucket blocking repeated losing entries |
| Belief (P7) | Persistent learned fact from closed trades |
| Neural state | WAITING / OBSERVING / THINKING / EXECUTING |

---

*Fortress AI · Research dashboard · Not investment advice*
