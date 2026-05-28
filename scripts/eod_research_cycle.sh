#!/usr/bin/env bash
# EOD research cycle — promote/kill hypotheses from replay + adversarial stress (no manual step).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export TZ="${FORTRESS_SYSTEM_TZ:-America/New_York}"
export FORTRESS_SYSTEM_TZ="${FORTRESS_SYSTEM_TZ:-America/New_York}"
mkdir -p "${REPO_ROOT}/logs"
exec bash "${REPO_ROOT}/scripts/cron_run.sh" research_cycle \
  python3 "${REPO_ROOT}/scripts/run_research_cycle.py" --sessions 5 --apply
