#!/usr/bin/env bash
# Run skim analysis every 30 minutes during RTH; auto-tune when metrics breach thresholds.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${ROOT}/data/skim_swarm/monitor.log"
mkdir -p "$(dirname "$LOG")"

while true; do
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[$ts] skim monitor cycle" >> "$LOG"
  if python3 "$ROOT/scripts/skim_swarm_analyze.py" --minutes 30 --auto-tune >> "$LOG" 2>&1; then
    :
  else
    echo "[$ts] analyze failed" >> "$LOG"
  fi
  sleep 1800
done
