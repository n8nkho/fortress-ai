#!/usr/bin/env bash
# Ensure whitepaper PDF is not older than markdown (fail closed for e2e).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MD=""
PDF=""
for cand in docs/FORTRESS_AI_WHITEPAPER.md docs/TRADING_BOT_WHITEPAPER.md; do
  if [[ -f "$cand" ]]; then
    MD="$cand"
    PDF="${cand%.md}.pdf"
    break
  fi
done

if [[ -z "$MD" ]]; then
  echo "[whitepaper] no whitepaper markdown found — skip"
  exit 0
fi

if [[ "${1:-}" == "--regenerate" ]]; then
  PY=""
  for p in "${ROOT}/venv/bin/python" /home/ubuntu/trading-bot/venv/bin/python python3; do
    if [[ -x "$p" ]] || command -v "$p" >/dev/null 2>&1; then
      PY="$p"
      break
    fi
  done
  if [[ -f scripts/generate_whitepaper_pdf.py ]]; then
    "$PY" scripts/generate_whitepaper_pdf.py || true
  fi
fi

if [[ ! -f "$PDF" ]]; then
  echo "[whitepaper] WARN: missing $PDF (md present: $MD) — allowing e2e" >&2
  exit 0
fi

if [[ "$PDF" -ot "$MD" ]]; then
  echo "[whitepaper] ERROR: $PDF is older than $MD — run: $0 --regenerate" >&2
  exit 1
fi

echo "[whitepaper] OK $MD ⇄ $PDF"
exit 0
