#!/usr/bin/env bash
# RTH loop — run autonomous SI every 30 minutes during US equity regular session.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export TZ="${FORTRESS_SYSTEM_TZ:-America/New_York}"
export FORTRESS_SYSTEM_TZ="${FORTRESS_SYSTEM_TZ:-America/New_York}"

INTERVAL="${FORTRESS_RTH_SI_INTERVAL_SEC:-1800}"
LOG="${ROOT}/data/rth_intraday_si/loop.log"
mkdir -p "$(dirname "$LOG")"

PY=""
for cand in "${ROOT}/venv/bin/python3" "${ROOT}/.venv/bin/python3"; do
  [[ -x "$cand" ]] && PY="$cand" && break
done
[[ -z "$PY" ]] && PY="python3"

_is_rth() {
  "$PY" -c "from utils.us_equity_hours import is_us_equity_rth_et; import sys; sys.exit(0 if is_us_equity_rth_et() else 1)"
}

while true; do
  if _is_rth; then
    ts="$(date -Iseconds)"
    echo "[$ts] rth_intraday_si cycle start" >> "$LOG"
    if bash "${ROOT}/scripts/cron_run.sh" rth_intraday_si python3 "${ROOT}/scripts/rth_intraday_si.py" >> "$LOG" 2>&1; then
      echo "[$ts] rth_intraday_si cycle ok" >> "$LOG"
    else
      echo "[$ts] rth_intraday_si cycle failed" >> "$LOG"
    fi
    sleep "$INTERVAL"
  else
    sleep 120
  fi
done
