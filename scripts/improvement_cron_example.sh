#!/usr/bin/env bash
# Example cron — daily governance maintenance (6 AM server time).
# Adjust paths and venv for your VM.
#
# 0 6 * * * /home/ubuntu/fortress-ai/scripts/improvement_cron_example.sh >> /home/ubuntu/fortress-ai/logs/improvement_cron.log 2>&1
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"

"$PYTHON" <<'PY'
from utils.improvement_governance import ImprovementGovernance

ImprovementGovernance().process_expired_veto_windows()
PY

"$PYTHON" <<'PY'
from agents.performance_monitor import PerformanceMonitor

PerformanceMonitor().monitor_active_changes()
PY
