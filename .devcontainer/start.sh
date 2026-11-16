#!/usr/bin/env bash
# Starts the dev stack every time the devcontainer boots (postStartCommand).
# Idempotent: skips anything that is already running.
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOGS="$ROOT/.devcontainer/logs"
mkdir -p "$LOGS"

# .env from the template (contains dev defaults such as GRACE_LOGINS=100000)
[ -f .env ] || cp .env.example .env

# Infrastructure. MinIO's images can be unavailable from the registries;
# Postgres + NATS are enough for everything except file storage.
docker compose up -d postgres nats minio minio-bucket >"$LOGS/compose.log" 2>&1 \
  || docker compose up -d postgres nats >>"$LOGS/compose.log" 2>&1

for _ in $(seq 1 30); do
  docker compose exec -T postgres pg_isready -U gaer >/dev/null 2>&1 && break
  sleep 1
done

echo running >"$LOGS/migrate.state"
if (cd server && ../.venv/bin/python -m app.migrate) >>"$LOGS/compose.log" 2>&1; then
  echo done >"$LOGS/migrate.state"
else
  echo failed >"$LOGS/migrate.state"
fi

# Python server on :8000 (reads .env)
if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  (cd server && nohup ../.venv/bin/uvicorn app.main:app --reload --port 8000 --env-file ../.env \
    >"$LOGS/server.log" 2>&1 &)
fi

# Vite on :5173 (proxies /api and /health to :8000)
if ! curl -sf http://localhost:5173 >/dev/null 2>&1; then
  nohup npm run dev --prefix web >"$LOGS/vite.log" 2>&1 &
fi

echo "Dev stack: http://localhost:5173 (app) · http://localhost:8000 (API) · logs in .devcontainer/logs/"
