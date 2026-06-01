# Skim Swarm

Always-on intraday service (`fortress-ai-skim-swarm`) trading up to **1 share** per symbol with adaptive rule-based long/short skims. **No LLM.**

## Universe (default)

`SPY, MSFT, GOOG, AMZN, AAPL, NASA, BRK.B, AGIX, LLY, V, MA, PLTR, CRWD`

AI infrastructure names (NVDA, AVGO, SOXX, etc.) live in **infra swarm** — see `docs/INFRA_SWARM.md`.

Each symbol is a **self-improving agent**: during RTH it continuously adapts from its own exits, blocks, and session P&amp;L (`data/skim_swarm/learned/{SYMBOL}.json`). Company/ETF context is cached under `data/skim_swarm/company_context/` (yfinance + static summaries).

### Continuous intraday SI (per symbol)

When `FORTRESS_SKIM_CONTINUOUS_SI=1` (default), each ticker maintains its own **session overlay** (entry delta boosts, stop/target mults, spread tolerance) that hot-reloads every wave via `get_params()`. Learning runs on:

- **Every exit** — micro-adapt stops/cooldowns; full `apply_adaptations` when `FORTRESS_SKIM_IMPROVE_EVERY_EXIT=1`
- **Block streaks** — repeated `no_entry` / spread / pattern blocks nudge that symbol's overlay
- **Shadow lane** — counterfactual tighter-stop PnL logged to `experience/{SYMBOL}_shadow.jsonl`; promotes when shadow beats live

Session dollar expectancy is prioritized over winning-pattern share when the session is losing (`FORTRESS_SKIM_SESSION_EXPECTANCY_MIN_USD`).

Legacy batch tuning still applies when continuous SI is off: after every 10 closed trades (then every 5).

### Phase 1 — pattern curation (now)

Goal: **≥75% of enabled patterns per symbol have positive net PnL** (`FORTRESS_SKIM_TARGET_WINNING_PATTERN_SHARE=0.75`). This is **not** trade win rate. Historical seeds: `python3 scripts/skim_symbol_historical_verify.py --years 10 --apply`.

### Phase 2 — autoresearch (deferred)

Do **not** enable Karpathy-style strategy mutation until enough symbols pass Phase 1 on **live paper** data (not daily proxy alone). Gate env (threshold TBD):

```bash
# Example once validated: 12 of 15 universe symbols at ≥75% winning-pattern share
# FORTRESS_SKIM_AUTORESEARCH_MIN_WINNING_SYMBOLS=12
```

Until set, autoresearch stays off; recursive improvement is deterministic (`adaptive_policy.py`, causation, `disable_patterns`).

(`BRKB` in env aliases to `BRK.B`. `NASA` = Tema Space Innovators ETF.)

### Swarm session SI (cross-symbol)

When `FORTRESS_SKIM_SWARM_SESSION_SI=1` (default), `utils/swarm_session_si.py` aggregates session stats across all symbols:

| Mode | Trigger | Effect |
|------|---------|--------|
| **tight** | Negative session expectancy | Fewer open slots, higher entry deltas |
| **churn** | High exit count + low win rate | Same + slower cycle |
| **critical** | Severe loss / both anomalies | **`pause_new_entries: true`** — no new entries; exits still run |

Policy: `data/skim_swarm/session_policy.json`. Block marker: `swarm_session_critical_pause`.

### Universe guard

<a id="universe-guard"></a>

- **Configured universe** — `FORTRESS_SKIM_UNIVERSE` (env). Wave may union open **owned** positions + bar context (`SPY`, `SOXX`) for exits only.
- **Orphan block** — new entries on symbols outside configured universe → `orphan_symbol_outside_universe` (`utils/swarm_universe_guard.py`).
- **Boot purge** — flat state files for delisted/orphan symbols removed on agent start.
- **Metrics** — `latest_metric.json` has `configured_universe` (env) and `universe` (active wave). Drift alerts compare configured lists only (avoids false `swarm_universe_drift` on SOXX context).

### Edge quality

RR/cost/expectancy gates, broker brackets, session scorecard — see [EDGE_QUALITY.md](EDGE_QUALITY.md).

RTH autofix runs via `fortress-ai-rth-intraday-si` (30m) and may disable toxic patterns (e.g. `pullback_uptrend`) when payoff inverts.

## Enable on VM

```bash
cd /home/ubuntu/fortress-ai
# .env: FORTRESS_SKIM_DRY_RUN=0, FORTRESS_AI_SYMBOL_DENYLIST=<universe>
sudo cp deploy/fortress-ai-skim-swarm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fortress-ai-skim-swarm
sudo systemctl disable --now fortress-ai-spy-agent   # avoid duplicate SPY logic
sudo systemctl restart fortress-ai-dashboard
```

## Operations

- Logs: `journalctl -u fortress-ai-skim-swarm -f`
- State: `data/skim_swarm/`
- API: `GET /api/skim/status`
- One-shot test: `FORTRESS_SKIM_DRY_RUN=1 python3 agents/skim_swarm_agent.py --once`

Unified AI agent will not trade denylisted symbols — see [UNIFIED_AI.md](UNIFIED_AI.md).
