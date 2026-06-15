#!/usr/bin/env bash
# Operator status monitor — snapshot every 15 minutes (configurable).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export TZ="${FORTRESS_SYSTEM_TZ:-America/New_York}"
export FORTRESS_SYSTEM_TZ="${FORTRESS_SYSTEM_TZ:-America/New_York}"
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

INTERVAL="${FORTRESS_OPERATOR_STATUS_INTERVAL_SEC:-900}"
LOG="${ROOT}/data/operator_status/loop.log"
mkdir -p "$(dirname "$LOG")"

PY=""
for cand in "${ROOT}/venv/bin/python3" "${ROOT}/.venv/bin/python3"; do
  [[ -x "$cand" ]] && PY="$cand" && break
done
[[ -z "$PY" ]] && PY="python3"

while true; do
  ts="$(date -Iseconds)"
  echo "[$ts] operator status cycle start" >> "$LOG"
  if bash "${ROOT}/scripts/cron_run.sh" operator_status "${PY}" "${ROOT}/scripts/operator_status_report.py" >> "$LOG" 2>&1; then
    echo "[$ts] operator status cycle ok" >> "$LOG"
  else
    echo "[$ts] operator status cycle failed" >> "$LOG"
  fi
  sleep "$INTERVAL"
done
