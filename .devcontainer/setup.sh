#!/usr/bin/env bash
# One-time setup when the devcontainer is created (postCreateCommand).
# Its progress is shown on the start page (:5170) through logs/setup.state.
set -euo pipefail
cd "$(dirname "$0")/.."
LOGS=.devcontainer/logs
mkdir -p "$LOGS"
echo running >"$LOGS/setup.state"
trap 'echo failed >"$LOGS/setup.state"' ERR

{
  [ -f .env ] || cp .env.example .env
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install --quiet -r server/requirements.txt
  [ -d web/node_modules ] || npm ci --prefix web
} 2>&1 | tee "$LOGS/setup.log"

echo done >"$LOGS/setup.state"
