#!/usr/bin/env bash
# One-shot SPY intraday agent smoke (dry-run friendly).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"
export FORTRESS_SPY_DRY_RUN="${FORTRESS_SPY_DRY_RUN:-1}"
out=$(python3 agents/spy_intraday_agent.py --once 2>&1)
echo "$out"
if echo "$out" | grep -q '"action": "idle"'; then
  echo "smoke_spy_agent OK (idle outside RTH/weekend)"
  exit 0
fi
test -f data/spy_intraday/decisions.jsonl
echo "smoke_spy_agent OK"
