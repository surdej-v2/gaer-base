# Gær — instructions for AI assistants

Gær is the lightweight version of Surdej: **one container** with a Python server (FastAPI) and a Vite frontend
(React + TypeScript), connected to a **shared managed PostgreSQL** (own database per container) and
**Object Storage** on OVHcloud. Login is phone number + one-time code, passkey after three logins, with a full
auth log and audit log. Every customer gets their own fork; updates are pulled from gaer-base.

## The backend already exists
Do not scaffold a new backend, database or login. Everything below is in place and tested
(skill `gaer-backend` has the details and code examples):

- **Login** — phone number → SMS code → session cookie; three grace logins, then passkey. Roles `user` and
  `admin`, first user becomes admin. Guard endpoints with `current_user` or `require_admin` from
  `app.sessions`. Frontend reads the user with `useAuth()` (`web/src/auth.tsx`).
- **Postgres** — the container has its own database. Locally `docker compose up -d` (postgres:16 on :5432,
  user/password/db `gaer`); in production a database on the managed cluster that only this container reaches.
  psycopg 3, raw SQL, no ORM. Schema in `server/app/migrations/*.sql`, applied by `python -m app.migrate`.
- **Python** — 3.12+ (image: 3.13), FastAPI + uvicorn, pinned `server/requirements.txt`, venv in `.venv`,
  settings from environment variables in `server/app/config.py`, tests with pytest against real Postgres.

Never add: another API server (Node/Express, Next API routes), another database (SQLite, Supabase, Firebase),
an ORM (Prisma, SQLAlchemy), or an auth library (Auth0, NextAuth, Clerk). Never add a dev login bypass —
use `SMS_PROVIDER=console`.

## Map
| Path | What |
| --- | --- |
| `server/app/main.py` | app, CSRF + security headers, CSP per path, SPA fallback, `/health` |
| `server/app/routes/` | `auth` login · `me` account · `files` · `clips` + bridge consent · `public` config, consent, analytics, GDPR form · `admin` /api/admin · `impersonation` test accounts · `features` rings + usage · `app_store` App Stores · `office` manifests · `web` /w website + extension zip |
| `server/app/sessions.py` | session cookie, `Principal`, `current_user` / `require_admin` / `any_session` |
| `server/app/db.py` | connection pools: `db.connection()`, `db.purge_connection()`, `fetch_one`, `fetch_all` |
| `server/app/config.py` | all settings from environment variables |
| `server/app/migrations/` | plain SQL, applied by `python -m app.migrate` |
| `server/app/logs.py`, `retention.py` | auth/audit log writers, purge + daily maintenance |
| `server/tests/` | pytest against real Postgres (`TEST_DATABASE_ADMIN_URL`) |
| `web/src/` | `pages/` app · `admin-app/` the standalone /admin app (own entry `web/admin.html`, spec in `specs/admin-app/`) · `features.tsx` FeatureGate · `mobile/` /m · `extension/` /extension · `office/` /office-addin · `bridge/` postMessage clients |
| `extension/` | MV3 browser extension (side panel) |
| `deploy/` | `deploy.sh` (on the instance), `Caddyfile`, `provision_database.py`, `.env.example` |
| `.github/workflows/` | `test.yml` (PRs), `deploy.yml` (push to main → prod) |
| `mobile/` | Expo app (iOS + Android): native login, then `/m` in a WebView — spec in `specs/mobile/` |
| `server/app/mcp/` | MCP servers: `/mcp` (appen, as the user) and `/mcp/admin` (admin, drafts) — spec in `specs/mcp/` |

## Rules that always apply
1. **Login rules are fixed** (skill `gaer-auth`). Never add a way around the code, the grace logins or the passkey.
2. **Every data change is audited** in the same transaction (skill `gaer-audit`). Logs are append-only; only the
   purge database user deletes, and the deletion is logged.
3. **Admin = admin role.** Everything under `/api/admin` is guarded and audited.
4. **Personal data** goes into the GDPR export and is removed on erasure. Analytics only identify a visitor
   after consent. Nothing is sent to third parties.
5. **Migrations are new files**; never edit an applied one.
6. **No secrets in the repo.** Configuration comes from environment variables (`server/app/config.py`).
7. Before you say you are done: `pytest server` and `npm run build --prefix web` pass.
8. UI text is Danish.

## Workflows (slash commands)
`/gaer-boot` read in · `/gaer-init` new project · `/gaer-dev` run locally · `/gaer-db` migrations ·
`/gaer-mix` mix in a recipe · `/gaer-new-feature` · `/gaer-pr` · `/gaer-pr-review` · `/gaer-release` ·
`/gaer-recover` · `/gaer-whatsup`. Source: `.gaer/agents/workflows/`.

## Skills
`gaer-backend`, `gaer-auth`, `gaer-audit`, `gaer-python-api`, `gaer-vite`, `gaer-storage`, `gaer-integrations`, `gaer-mcp`.
Source: `.gaer/agents/skills/*/SKILL.md`.
