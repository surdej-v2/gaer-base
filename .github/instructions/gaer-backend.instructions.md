---
applyTo: "server/**,web/src/api.ts,web/src/auth.tsx,docker-compose.yml,Dockerfile,.env.example"
description: The Gær backend already exists — login, the container's own Postgres database and the Python setup.
---

Read and follow `.gaer/agents/skills/gaer-backend/SKILL.md` before you change anything here.

- Login is done: phone + SMS code, three grace logins, then passkey. Guard endpoints with `current_user` or
  `require_admin` from `app.sessions`. Never add a bypass or another auth library.
- The container has its own Postgres database (Docker locally, OVHcloud managed cluster in production).
  psycopg 3, raw SQL, `async with db.connection()`. Schema changes are new files in `server/app/migrations/`.
- Python 3.12+, FastAPI, pinned `server/requirements.txt`, settings from env in `server/app/config.py`.
- `pytest server` must pass against a real Postgres.
