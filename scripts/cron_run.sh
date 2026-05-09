#!/usr/bin/env bash
# Run orchestrator or an arbitrary repo-root command under venv; writes cron heartbeats.
#
# Usage:
#   cron_run.sh <job_name>                                  → orchestrator.py <job_name>
#   cron_run.sh <job_name> --dry-run ...                   → orchestrator.py <job_name> ...
#   cron_run.sh <job_name> python3 -m agents.some_module   → direct module run (heartbeat on exit)
#
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY=""
for cand in "${REPO_ROOT}/venv/bin/python3" "${REPO_ROOT}/.venv/bin/python3"; do
  if [[ -x "$cand" ]]; then
    PY="$cand"
    break
  fi
done
[[ -z "${PY}" ]] && PY="python3"

JOB_NAME="${1:-unknown}"
shift || true

_hb_ok() {
  "$PY" -u "${REPO_ROOT}/utils/cron_heartbeat.py" --job "$JOB_NAME" --ok || true
}

_hb_fail() {
  local rc="$1"
  local detail="${2:-non-zero exit}"
  "$PY" -u "${REPO_ROOT}/utils/cron_heartbeat.py" --job "$JOB_NAME" --failure "$rc" --detail "$detail" || true
}

_is_python_invocation() {
  local x="${1:-}"
  [[ -z "$x" ]] && return 1
  [[ "$x" == "python3" ]] || [[ "$x" == "python" ]] || [[ "$x" == "$PY" ]] || [[ "$(basename "$x")" == "python3" ]] || [[ "$(basename "$x")" == "python" ]]
}

_run_orchestrator() {
  if [[ ! -f "${REPO_ROOT}/orchestrator.py" ]]; then
    echo "cron_run.sh: orchestrator.py not found in ${REPO_ROOT}" >&2
    _hb_fail 127 "no orchestrator.py"
    exit 127
  fi
  set +e
  "$PY" -u "${REPO_ROOT}/orchestrator.py" "$@"
  local rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    _hb_ok
  else
    _hb_fail "$rc" "orchestrator non-zero exit"
  fi
  exit "$rc"
}

_run_command() {
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    _hb_ok
  else
    _hb_fail "$rc" "command non-zero exit"
  fi
  exit "$rc"
}

# Single-token quoted cron lines: entire shell command as one argument ("python3 -m …").
if [[ "$#" -eq 1 ]] && [[ "$1" == *" "* ]] && [[ "$1" == python3\ * || "$1" == python\ * ]]; then
  _run_command bash -c "$1"
fi

if [[ "${JOB_NAME}" == "screen" && "${FORTRESS_UPLIFT_AUTO_PROMOTE_AFTER_SCREEN:-0}" == "1" ]]; then
  set +e
  "$PY" -u "${REPO_ROOT}/orchestrator.py" "$JOB_NAME" "$@"
  screen_rc=$?
  set -e
  "$PY" -u "${REPO_ROOT}/orchestrator.py" uplift_auto_promote --apply --required-clean-sessions "${FORTRESS_UPLIFT_REQUIRED_CLEAN_SESSIONS:-5}" || true
  if [[ "$screen_rc" -eq 0 ]]; then
    _hb_ok
  else
    _hb_fail "$screen_rc" "orchestrator non-zero exit"
  fi
  exit "$screen_rc"
fi

if [[ "$#" -eq 0 ]]; then
  if [[ ! -f "${REPO_ROOT}/orchestrator.py" ]]; then
    echo "cron_run.sh: missing orchestrator.py under ${REPO_ROOT}; pass a python command after job_name." >&2
    _hb_fail 127 "no orchestrator.py"
    exit 127
  fi
  _run_orchestrator "$JOB_NAME"
fi

if _is_python_invocation "$1"; then
  _run_command "$@"
fi

_run_orchestrator "$JOB_NAME" "$@"
