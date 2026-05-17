#!/usr/bin/env bash
# Recreate .venv on Linux (fixes macOS Mach-O venv copied to the server).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi
echo "Using: $($PY --version) at $(command -v "$PY")"
if [[ -d .venv ]] && file .venv/bin/python3 2>/dev/null | grep -q Mach-O; then
  echo "Removing macOS .venv …"
  rm -rf .venv
fi
"$PY" -m venv .venv
.venv/bin/pip install -U pip wheel
.venv/bin/pip install -r requirements.txt
echo "OK: $(.venv/bin/python3 --version) $(file .venv/bin/python3 | head -1)"
