#!/usr/bin/env bash
# End-to-end verification for fortress-ai — run before git commit / deploy.
# - Full unit test suite (deterministic)
# - Domain ingest runner + ingest_health.json artifact check (network optional for some sources)
#
# Usage:
#   ./scripts/e2e_verify.sh
#   ./scripts/e2e_verify.sh --no-ingest   # skip network ingest (tests only)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:-}:${ROOT}"

SKIP_INGEST="0"
for arg in "$@"; do
  case "$arg" in
    --no-ingest) SKIP_INGEST="1" ;;
    -h|--help)
      echo "Usage: $0 [--no-ingest]"
      exit 0
      ;;
  esac
done

echo "[e2e:fai] repo root: ${ROOT}"

echo "[e2e:fai] python unittest (tests/)..."
python3 -m unittest discover -s tests -p 'test_*.py' -v

if [[ "${SKIP_INGEST}" == "1" ]]; then
  echo "[e2e:fai] skip ingest (--no-ingest)"
  echo "[e2e:fai] OK"
  exit 0
fi

echo "[e2e:fai] domain ingest runner..."
python3 -m agents.domain_ingest.ingest_runner >/dev/null

HP="${ROOT}/data/domain_intelligence/ingest_health.json"
if [[ ! -f "${HP}" ]]; then
  echo "[e2e:fai] ERROR: missing ${HP}" >&2
  exit 1
fi
echo "[e2e:fai] ingest_health.json OK ($(wc -c < "${HP}") bytes)"

echo "[e2e:fai] OK — safe to commit/deploy"
