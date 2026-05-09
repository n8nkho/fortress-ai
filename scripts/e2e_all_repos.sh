#!/usr/bin/env bash
# Run fortress-ai e2e, then trading-bot e2e if present (common VM layout).
#
# Env:
#   FORTRESS_TRADING_BOT_ROOT — override path (default: sibling trading-bot next to fortress-ai parent)
#
# Usage:
#   ./scripts/e2e_all_repos.sh
#   FORTRESS_INGEST_SKIP=1 ./scripts/e2e_all_repos.sh   # passed as --no-ingest to fortress-ai only
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ING_ARGS=()
if [[ "${FORTRESS_INGEST_SKIP:-}" == "1" ]]; then
  ING_ARGS+=(--no-ingest)
fi

"${HERE}/e2e_verify.sh" "${ING_ARGS[@]}"

FAI_ROOT="$(cd "${HERE}/.." && pwd)"
DEFAULT_TB="$(cd "${FAI_ROOT}/.." && pwd)/trading-bot"
TB="${FORTRESS_TRADING_BOT_ROOT:-$DEFAULT_TB}"
if [[ -x "${TB}/scripts/e2e_before_deploy.sh" ]]; then
  echo "[e2e:all] running ${TB}/scripts/e2e_before_deploy.sh"
  "${TB}/scripts/e2e_before_deploy.sh"
else
  echo "[e2e:all] skip trading-bot (no ${TB}/scripts/e2e_before_deploy.sh)"
fi

echo "[e2e:all] OK — both stacks verified"
