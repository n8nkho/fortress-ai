# Fortress AI

Parallel **single-agent (DeepSeek)** stack for research and A/B comparison against **Classic Fortress** (multi-agent). This directory is **fully isolated**: separate ports, data, logs, and Alpaca credentials.

## Layout

| Path | Purpose |
|------|---------|
| `agents/unified_ai_agent.py` | Main observe → reason → act loop |
| `utils/pre_trade_gate.py` | Same submission gate logic as Classic (mandatory before orders) |
| `utils/operator_halt.py` | Kill switch file + env halt |
| `dashboard/ai_command_center.py` | Dashboard (port from `FORTRESS_AI_DASHBOARD_PORT`, e.g. **8050**) |
| `deploy/` | systemd unit examples + [deploy/README.md](deploy/README.md) |
| `data/` | `ai_state.json`, `ai_decisions.jsonl`, `ai_metrics.jsonl`, cost ledger |

Deploy path on server: `/home/ubuntu/fortress-ai` (copy this tree).

## Phased rollout

| Phase | Trading | Confidence gate |
|-------|---------|-----------------|
| Week 1 | **Dry-run only** (`FORTRESS_AI_DRY_RUN=1`) | N/A (no submits) |
| Paper (conservative) | Alpaca **paper** executes (`FORTRESS_AI_DRY_RUN=0`) | **0.85–0.92** — fewer trades, smaller sizes (`FORTRESS_MAX_ORDER_NOTIONAL_USD`) |
| Later experiments | Paper | Lower toward **0.75–0.8** only if you want more activity |

**Paper trading on a server:** see **[deploy/README.md](deploy/README.md)** (systemd). No strategy guarantees “high win ratio” or “near zero losses”; use caps + halt + monitoring to limit damage.


## Cost controls

- Default loop: **every 5 minutes** (`FORTRESS_AI_LOOP_SECONDS=300`)
- Prompt budget: `FORTRESS_AI_MAX_PROMPT_CHARS` / `FORTRESS_AI_MAX_OBS_CHARS`
- **Weekly** LLM spend cap: `FORTRESS_AI_WEEKLY_COST_CAP_USD` (default **$1**); agent **stops** when exceeded

## Safety

- All orders call **`evaluate_pre_trade_submission`** from `utils/pre_trade_gate.py` (copied behavior from Classic).
- **Kill switch**
  - **Env** (applies to any process): `FORTRESS_TRADING_HALT=1`
  - **File** (per instance): `data/operator_trading_halt.json`
  - **Shared file with Classic** (optional): set **`FORTRESS_SHARED_HALT_PATH`** on both instances to the same absolute path.

## Commands

```bash
cd /home/ubuntu/fortress-ai    # server deploy path — use your local clone path instead when developing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit keys

# Dry-run single cycle (Week 1)
export PYTHONPATH=.
python3 agents/unified_ai_agent.py --dry-run --once

# Dashboard (futuristic UI — SSE + expert mode + Classic comparison drawer)
export PYTHONPATH=.
python3 dashboard/ai_command_center.py
# http://127.0.0.1:8050/          dashboard (set FORTRESS_AI_DASHBOARD_PORT)
# http://127.0.0.1:8050/mockup    static layout preview (dummy data)

# Key API routes: GET /api/ai/current_state, GET /api/comparison, GET /api/stream/decisions (SSE), GET /api/export/bundle, GET /api/charts/dashboard, GET /api/expert/bundle

# After deploy — smoke-test against a *running* dashboard (HTTP curls):
./scripts/smoke_dashboard.sh http://127.0.0.1:8050

# Same dashboard API checks without a running server (stdlib unittest; no pytest required):
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_dashboard_api.py' -q

# Comparison metrics (optional Classic path)
export CLASSIC_DATA_DIR=/path/to/trading-bot/data
export PYTHONPATH=.
python3 scripts/compare_systems.py
```

## Fallback policy (research)

After ~4 weeks paper comparison: keep Classic only if AI underperforms; consider hybrid if AI wins on some metrics; if tied, analyze slice-by-slice (latency, cost, regime). Documented in `docs/COMPARISON_METRICS.md`.

## Classic Fortress unchanged

Do **not** modify the Classic repo; run both side-by-side with different `.env` and ports (**8083** Classic vs **8050** AI by default).
