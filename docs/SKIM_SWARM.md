# Skim Swarm

Always-on intraday service (`fortress-ai-skim-swarm`) trading up to **1 share** per symbol with adaptive rule-based long/short skims. **No LLM.**

## Universe (default)

`SPY, NVDA, MSFT, GOOG, AMZN, AAPL, SOXX, NASA, BRK.B, AGIX, AVGO, LLY, V, MA, PLTR, CRWD`

Each symbol is a **self-improving agent**: after every 3 closed trades it adjusts entry thresholds, skim targets, and cooldowns from win rate and P&amp;L (`data/skim_swarm/learned/`). Company/ETF context is cached under `data/skim_swarm/company_context/` (yfinance + static summaries).

(`BRKB` in env aliases to `BRK.B`. `NASA` = Tema Space Innovators ETF.)

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

Unified AI agent will not trade denylisted symbols.
