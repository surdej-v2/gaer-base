---
name: gaer-dev
description: Start Postgres, MinIO, the Python server and Vite locally against a development database.
---

# /gaer-dev

1. Infrastructure (Postgres on 5432, MinIO on 9000/9001, NATS with JetStream on 4222, monitoring on 8222,
   ClickHouse over HTTP on 8123):
   ```bash
   docker compose up -d
   ```
2. Migrate (Postgres, then ClickHouse when `CLICKHOUSE_URL` is set):
   ```bash
   (cd server && python -m app.migrate)
   ```
3. Python server on :8000 with reload (reads `.env`):
   ```bash
   cd server && uvicorn app.main:app --reload --port 8000 --env-file ../.env
   ```
4. Vite on :5173 (proxies `/api`, `/health` to :8000):
   ```bash
   npm run dev --prefix web
   ```
5. Open http://localhost:5173/login. With `SMS_PROVIDER=console` the code is printed in the server log and shown on the login page.
   The first user on a new database becomes admin.
6. Surfaces to check: `/` (app), `/admin`, `/w/` (served by Python — use http://localhost:8000/w/), `/m`, `/extension`, `/office-addin`.
7. To run the whole container as in production: `docker compose --profile app up --build`, then http://localhost:8000.
