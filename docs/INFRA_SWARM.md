# Infra Swarm

Always-on intraday service (`fortress-ai-infra-swarm`) for **pure AI infrastructure** symbols using **Stack Residual Propagation (SRP)** — no LLM.

## Strategy (SRP)

Each symbol is a native recursive agent (`data/infra_swarm/learned/{SYMBOL}.json`) with layer-aware patterns:

- `layer_catch_up_long` / `layer_catch_up_short` — L1 lead, symbol lagging its layer basket
- `layer_rip_fade` — symbol rich vs layer residual
- `equipment_capex_confirm` — L3 pullback when L1+L3 aligned
- `power_parity` — L4 enabler lead when compute flat
- `stack_momentum_long` — L1 momentum when stack stress high

Anchor basket: **SMH** (`FORTRESS_INFRA_ANCHOR`).

## Adaptive universe

Candidate pool (L1–L4) is scored by lifetime/session expectancy. Active universe is rewritten to `data/infra_swarm/adaptive_universe.json` with layer balance caps.

## Enable

```bash
sudo cp deploy/fortress-ai-infra-swarm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fortress-ai-infra-swarm
sudo systemctl restart fortress-ai-dashboard
```

## Operations

- Logs: `journalctl -u fortress-ai-infra-swarm -f`
- State: `data/infra_swarm/`
- API: `GET /api/infra/status`
- One-shot: `FORTRESS_INFRA_DRY_RUN=1 python3 agents/infra_swarm_agent.py --once`

Partition from skim: NVDA, AVGO, SOXX removed from skim universe; all infra symbols on `FORTRESS_AI_SYMBOL_DENYLIST`.
