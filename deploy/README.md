# Fortress AI — server deploy (Ubuntu)

## Expectations

Conservative settings in `.env.example` **reduce size and frequency of risk**; they do **not** guarantee a high win rate or zero losses. Treat paper trading as a **safety rehearsal** for process, gates, and monitoring.

## One-time on the VM

```bash
cd /home/ubuntu/fortress-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env   # Alpaca paper, DeepSeek, FORTRESS_AI_DRY_RUN=0 for paper orders, etc.
```

Paper trading checklist:

- `ALPACA_BASE_URL=https://paper-api.alpaca.markets` (single `=` per line for secrets).
- `FORTRESS_DOTENV_OVERRIDE=1` if your shell exports different `ALPACA_*`.
- `DEEPSEEK_API_KEY` set.
- `FORTRESS_AI_DRY_RUN=0` to allow Alpaca **paper** submits (still blocked by `pre_trade_gate` and confidence).

Smoke tests:

```bash
export PYTHONPATH=.
python3 agents/unified_ai_agent.py --dry-run --once
curl -s http://127.0.0.1:${FORTRESS_AI_DASHBOARD_PORT:-8050}/api/health

# Charts + expert APIs (optional — requires dashboard running):
#   cd /home/ubuntu/fortress-ai && ./scripts/smoke_dashboard.sh
```

## systemd (recommended)

Adjust **User** and **paths** in the unit files if your Linux user or install dir differs.

```bash
sudo cp deploy/fortress-ai-agent.service deploy/fortress-ai-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fortress-ai-dashboard fortress-ai-agent
sudo systemctl status fortress-ai-agent fortress-ai-dashboard
journalctl -u fortress-ai-agent -f
```

Stop / disable:

```bash
sudo systemctl stop fortress-ai-agent
sudo systemctl disable fortress-ai-agent
```

## Dashboard security (LAN or public VM)

1. **HTTP Basic in the app (simplest)** — In `~/fortress-ai/.env` set **both**:

   - `FORTRESS_AI_DASHBOARD_BASIC_USER=...`
   - `FORTRESS_AI_DASHBOARD_BASIC_PASSWORD=...` (long random string)

   Restart `fortress-ai-dashboard`. The browser will prompt for a password; same protection applies to `/api/*` and static files.

   **Plain HTTP** (port 8050 without TLS) means the password is only **Base64-encoded**, not encrypted—anyone on the same Wi‑Fi or path can sniff it. For real privacy use step 2.

2. **HTTPS in front (recommended for a public IP)** — Keep Flask on `127.0.0.1:8050` and put **nginx** (or Caddy) on 443 with a certificate (Let’s Encrypt). See `deploy/nginx-fortress-dashboard.example.conf` for a starting template. Set `FORTRESS_AI_DASHBOARD_HOST=127.0.0.1` so Flask does not listen on the public interface.

3. **Firewall** — Allow only your IP to TCP 8050 or 443 in the cloud security list.

4. **Optional** — `FORTRESS_AI_DASHBOARD_AUTH_EXEMPT_HEALTH=1` if a monitor must call `GET /api/health` without Basic auth.

## Kill switch

- Dashboard/API or env: see main `README.md` (`FORTRESS_TRADING_HALT`, halt file).
