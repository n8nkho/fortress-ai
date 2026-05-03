#!/usr/bin/env bash
# Fortress AI dashboard API smoke checks (matches manual curl validation).
# Usage:
#   ./scripts/smoke_dashboard.sh
#   ./scripts/smoke_dashboard.sh http://127.0.0.1:8050
#   FORTRESS_AI_DASHBOARD_PORT=8050 ./scripts/smoke_dashboard.sh
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:${FORTRESS_AI_DASHBOARD_PORT:-8050}}"
BASE_URL="${BASE_URL%/}"

echo "Smoke: ${BASE_URL}"

curl -sf "${BASE_URL}/api/health" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok') is True, d"

curl -sf "${BASE_URL}/api/charts/dashboard" | python3 -c "
import json, sys
d = json.load(sys.stdin)
spy = d['spy']
assert set(spy.keys()) >= {'change_pct', 'labels', 'prices'}, spy.keys()
lc = d['llm_cost']
labels = lc.get('labels') or []
assert len(labels) == 14, len(labels)
print('spy keys:', list(spy.keys()))
print('llm labels:', len(labels))
"

curl -sf "${BASE_URL}/api/expert/bundle" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tail = d.get('cost_ledger_tail')
assert isinstance(tail, list), type(tail)
print('ledger rows:', len(tail))
"

echo "OK — dashboard APIs passed."
