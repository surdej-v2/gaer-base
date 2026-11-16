---
name: gaer-dev
description: Start Postgres, MinIO, the Python server and Vite locally against a development database.
---

# /gaer-dev

1. Infrastructure (Postgres on 5432, MinIO on 9000/9001, NATS with JetStream on 4222, monitoring on 8222):
   ```bash
   docker compose up -d
   ```
2. Migrate:
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
5. Open http://localhost:5173/login. With `MAIL_PROVIDER=console` the code is printed in the server log and shown on the login page,
   and the page offers demo accounts for every role (`server/app/demo_logins.py`) that log in through the same one-time code.
   The first user on a new database becomes admin.
6. Surfaces to check: `/` (app), `/admin`, `/w/` (served by Python — use http://localhost:8000/w/), `/m`, `/extension`, `/office-addin`.
7. To run the whole container as in production: `docker compose --profile app up --build`, then http://localhost:8000.

## In a Codespace

The devcontainer (`.devcontainer/`, Python 3.13 + Node 22 + Docker) does all of the above by itself. Within a few
seconds the start page on :5170 opens in the editor's preview (`.devcontainer/startside/`, standard library only):
in the browser it shows how to open the codespace in VS Code Desktop; in VS Code Desktop it follows setup, Postgres,
migrations, the API and Vite, then switches to http://localhost:5173/login. `?mode=web|desktop` forces a view,
`?stay` stops the switch. Logs are in `.devcontainer/logs/`.
