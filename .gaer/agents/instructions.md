# Gær — instructions for AI assistants

Gær is the lightweight version of Surdej: **one container** with a Python server (FastAPI) and a Vite frontend
(React + TypeScript), connected to a **shared managed PostgreSQL** (own database per container) and
**Object Storage** on OVHcloud. Login is phone number + one-time code, passkey after three logins, with a full
auth log and audit log. Every customer gets their own fork; updates are pulled from gaer-base.

## Map
| Path | What |
| --- | --- |
| `server/app/main.py` | app, CSRF + security headers, CSP per path, SPA fallback, `/health` |
| `server/app/routes/` | `auth` login · `me` account · `files` · `clips` + bridge consent · `public` config, consent, analytics, GDPR form · `admin` /admin · `office` manifests · `web` /w website + extension zip |
| `server/app/migrations/` | plain SQL, applied by `python -m app.migrate` |
| `server/app/ai.py`, `routes/ai.py` | optional AI chat (OVHcloud AI Endpoints), client tools, usage + monthly limits |
| `server/app/logs.py`, `retention.py` | auth/audit log writers, purge + daily maintenance |
| `server/tests/` | pytest against real Postgres (`TEST_DATABASE_ADMIN_URL`) |
| `web/src/` | `pages/` app · `admin/` /admin · `mobile/` /m · `extension/` /extension · `office/` /office-addin · `bridge/` postMessage clients |
| `extension/` | MV3 browser extension (side panel) |
| `deploy/` | `deploy.sh` (on the instance), `Caddyfile`, `provision_database.py`, `.env.example` |
| `.github/workflows/` | `test.yml` (PRs), `deploy.yml` (push to main → prod) |

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
`gaer-auth`, `gaer-audit`, `gaer-python-api`, `gaer-vite`, `gaer-storage`, `gaer-integrations`.
Source: `.gaer/agents/skills/*/SKILL.md`.
