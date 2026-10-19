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
| `server/app/ai.py`, `routes/ai.py` | optional AI chat (OVHcloud AI Endpoints), client tools, usage + monthly limits |
| `server/app/logs.py`, `retention.py` | auth/audit log writers, purge + daily maintenance |
| `server/app/gdpr.py`, `reapply_erasures.py` | GDPR export (zip) and erasure; re-applying erasures after a backup restore |
| `server/tests/` | pytest against real Postgres (`TEST_DATABASE_ADMIN_URL`) |
| `web/src/` | `pages/` app · `admin-app/` the standalone /admin app (own entry `web/admin.html`, spec in `specs/admin-app/`) · `features.tsx` FeatureGate · `mobile/` /m · `extension/` /extension · `office/` /office-addin · `bridge/` postMessage clients |
| `extension/` | MV3 browser extension (side panel) |
| `deploy/` | `deploy.sh` (on the instance), `Caddyfile`, `provision_database.py`, `.env.example` |
| `.github/workflows/` | `test.yml` (PRs), `deploy.yml` (push to main → prod) |
| `mobile/` | Expo app (iOS + Android): native login, then `/m` in a WebView — spec in `specs/mobile/` |
| `server/app/mcp/` | MCP servers: `/mcp` (appen, as the user) and `/mcp/admin` (admin, drafts) — spec in `specs/mcp/` |

## Routes — one server, many surfaces
Everything is served by the one Python server on one domain. Which surface answers depends on the path.
They share the same login (session cookie), the same database and the same API.

| Route | Who | What | Code |
| --- | --- | --- | --- |
| `/` | logged in | The web app's home. **Anonymous visitors are redirected to `/w/`** (by the server, and by the SPA on client-side navigation). | `main.py` `spa()`, `web/src/pages/Home.tsx` |
| `/w/…` | everyone | **Public website**, server-rendered HTML from Markdown pages (`web_pages`), da/en via `?lang=` + `w_lang` cookie. The front page always ends with a **login card**; on a new instance (no users, no `BOOTSTRAP_ADMIN_PHONE`) it says the first person to log in becomes admin. A placeholder is shown until a front page is written. | `routes/web.py`, `web_static/w.css`, `w.js` |
| `/login` | anonymous | Phone → one-time code → offer/require passkey (see `gaer-auth`). Shows the first-user/admin notice when `first_user` is true. Also `/extension/login` and `/m/login` (same page, embedded layout). | `pages/Login.tsx` |
| `/klip`, `/filer`, `/konto`, `/privatliv` | logged in (privatliv: everyone) | Clips, files in Object Storage, account (passkeys, sessions), privacy + consent + GDPR requests. | `pages/`, `clips.tsx` |
| `/admin/…` | admin role | **Standalone admin app** (own Vite entry `web/admin.html`): statistics, users + impersonation, features and rings, App Stores, auth/audit log, analytics, heatmaps, GDPR, retention, consent, files, website (`/admin/web`), AI (`/admin/ai`), extension & Office (`/admin/integrations`), settings. Every read and change is audited. | `admin-app/`, `routes/admin.py`, `impersonation.py`, `features.py`, `app_store.py` |
| `/m/…` | logged in | **Mobile app (PWA)**: home, AI chat (if enabled), clips, files with camera upload, profile. Bottom tab bar, own manifest + service worker scoped to `/m/`. | `mobile/Mobile.tsx`, `public/m/` |
| `/extension/…` | logged in, inside the browser extension | **Side panel** of the Chrome/Edge extension (iframe). Current page + per-domain consent + save as clip, AI chat with the page as context, clips, account. Talks to the panel over `gaer-bridge`. | `extension/` (both repo root and `web/src/extension/`), `bridge/extension.ts` |
| `/office-addin?app=word\|excel\|powerpoint\|outlook` | public shell | **Office router**: loads Office.js, detects the host, embeds the matching tool. Must stay public (Office closes panes that do not initialise). | `office/OfficeShell.tsx`, `office/handlers.ts` |
| `/extension/tools/word\|outlook\|document` | logged in, inside the Office shell | Office tools: insert/save clips and AI chat with the document or mail as context; Office.js calls go to the shell over `gaer-office-host`. | `office/Tools.tsx`, `bridge/officeHost.ts` |
| `/api/…` | per endpoint | JSON API. State-changing calls need the `X-Gaer: 1` header (CSRF). Groups: `auth`, `me`, `files`, `clips`, `bridge-consent`, `ai`, `public`, `consent`, `collect`, `office`, `extension/download`, `admin`. | `routes/` |
| `/health` | deploy | Database check + version; used by `deploy/deploy.sh` before switching versions. | `main.py` |

Rules for routes:
- A new page goes in `web/src/App.tsx` (or `web/src/admin-app/main.tsx` + `nav.ts` for /admin); pick the surface deliberately. Pages with their own chrome (`/m`, `/extension`,
  `/office-addin`) are listed in `OWN_CHROME`.
- Only `EMBEDDABLE` paths in `main.py` may be framed by the extension and Office hosts — add a path there only
  if it really must run inside them.
- Anything the public may see without login lives under `/w/`, `/login`, `/privatliv` or `/api/public/…`.
- AI is optional: UI for it must hide itself when `/api/ai/status` says `enabled: false`.

## Rules that always apply
1. **Login rules are fixed** (skill `gaer-auth`). Never add a way around the code, the grace logins or the passkey.
2. **Every data change is audited** in the same transaction (skill `gaer-audit`). Logs are append-only; only the
   purge database user deletes, and the deletion is logged.
3. **Admin = admin role.** Everything under `/api/admin` is guarded and audited.
4. **Personal data** goes into the GDPR export and is removed on erasure — both live in `server/app/gdpr.py`,
   used by `/admin/gdpr` and by users themselves (`/api/me/export`, `/api/me/delete`). A new table with personal
   data is not done until it is in both. Analytics only identify a visitor after consent. Nothing is sent to
   third parties.
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
