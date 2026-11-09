# Gær — instructions for AI assistants

Gær is the lightweight version of Surdej: **one container** with a Python server (FastAPI) and a Vite frontend
(React + TypeScript), with its **own PostgreSQL** (a container in the same compose stack, own database and roles)
and **S3 Object Storage**. Login is e-mail + one-time code with passkeys, security keys and authenticator apps
(MFA for /admin, opt-in for users), with a full auth log and audit log. Every customer gets their own fork; updates are pulled from gaer-base.

## The backend already exists
Do not scaffold a new backend, database or login. Everything below is in place and tested
(skill `gaer-backend` has the details and code examples):

- **Login** — e-mail (SMS opt-in) → one-time code → session cookie. Everyone is offered a passkey; users may opt
  in to MFA ("Kræv passkey"); /admin needs a second factor (passkey, security key or authenticator app) after
  three grace logins. Roles `user` and `admin`, first user becomes super admin. Guard endpoints with
  `current_user`, `require_admin` or `require_admin_stepup` from `app.sessions`. Frontend reads the user with
  `useAuth()` (`web/src/auth.tsx`); profile and security UI: skill `gaer-profile`.
- **Postgres** — the container has its own database. Locally `docker compose up -d` (postgres:16 on :5432,
  user/password/db `gaer`); in production the `postgres` service in `deploy/compose.yml` (roles from `postgres-init.sh`).
  psycopg 3, raw SQL, no ORM. Schema in `server/app/migrations/*.sql`, applied by `python -m app.migrate`.
- **Python** — 3.12+ (image: 3.13), FastAPI + uvicorn, pinned `server/requirements.txt`, venv in `.venv`,
  settings from environment variables in `server/app/config.py`, tests with pytest against real Postgres.

Never add: another API server (Node/Express, Next API routes), another database (SQLite, Supabase, Firebase),
an ORM (Prisma, SQLAlchemy), or an auth library (Auth0, NextAuth, Clerk). Never add a dev login bypass —
use `MAIL_PROVIDER=console` (and `SMS_PROVIDER=console`).

## Map
| Path | What |
| --- | --- |
| `server/app/main.py` | app, CSRF + security headers, CSP per path, SPA fallback, `/health` |
| `server/app/routes/` | `auth` login + second factor · `me` profile, avatar, account · `security` own factors + "Kræv passkey" · `files` own home + /shared · `admin_files` /api/admin/files tree · `clips` + bridge consent · `public` config, consent, analytics, GDPR form · `admin` /api/admin · `impersonation` test accounts · `features` rings + usage · `admin_ops` /api/admin webhooks (out and in), jobs, retention policies · `hooks` incoming webhooks · `review` review panel (notes, sessions, zip) · `app_store` App Stores · `data` /api/admin/data catalog, Udforsk, API sources · `nats` /api/admin/nats monitoring · `office` manifests · `web` /w website + extension zip · `user_area` /api/user Mit område + delegated access · `mail` /api/mail + `admin_mail` /api/admin/mail (mail module) |
| `server/app/sessions.py` | session cookie, `Principal`, `current_user` / `require_admin` / `require_admin_stepup` / `any_session` |
| `server/app/mfa.py`, `totp.py` | second-factor rules (who needs what, changing factors), authenticator apps (TOTP) |
| `server/app/filetree.py` | file tree over `files`: a home per user under `/users/<id>/` (`~/` = your own) and `/shared/` for project files; folders (`file_folders`), access rules, upload/write/move/delete with audit — used by `/api/files`, `/api/admin/files`, the AI chat and both MCP servers |
| `server/app/mailbox/` | Mail, calendar and contacts (`specs/mail/`): `backend.py` Stalwart client (or in memory, `MAILBOX_BACKEND`), `service.py` rules (provisioning, shared-mailbox and collection roles, identities, app passwords, 30-day retention), `dns.py` domain records + live check, `tasks.py` jobs `mail.*` and the `lettermint` incoming-webhook handler |
| `server/app/delegation.py` | Mit område (`/user`): sharing your area with another user as reader or author (`/user/<id>`), access checks, the activity list; delegates' changes are audited with `on_behalf_of` |
| `server/app/db.py` | connection pools: `db.connection()`, `db.purge_connection()`, `fetch_one`, `fetch_all` |
| `server/app/config.py` | all settings from environment variables |
| `server/app/migrations/` | plain SQL, applied by `python -m app.migrate` |
| `server/app/ai.py`, `routes/ai.py` | optional AI chat (OVHcloud AI Endpoints), client tools, usage + monthly limits |
| `server/app/logs.py`, `retention.py` | auth/audit log writers, purge of logs, cleanup of operational data (`retention_policies`) |
| `server/app/jobs.py` | background jobs: queue in Postgres (`jobs`), workers + scheduler (`job_schedules`) in the app process, retries with backoff; `@jobs.handler("kind")`, `jobs.enqueue(conn, …)` |
| `server/app/webhooks.py`, `secrets_box.py` | outgoing webhooks: `webhooks.emit(conn, event, data)` in the same transaction; signed (HMAC), optional auth header, retries via jobs, SSRF guard; secrets encrypted (AES-GCM) |
| `server/app/webhooks_in.py`, `routes/hooks.py` | incoming webhooks: `POST /api/hooks/in/<key>` (CSRF-exempt), verified per endpoint (Gær signature, HMAC header, fixed header such as Authorization, or URL only) + IP filter, size and rate limits, dedupe on delivery id; stored, then `@webhooks_in.handler("name")` runs as a job |
| `server/app/nats_monitor.py`, `routes/nats.py` | /admin/nats: reads NATS' HTTP monitoring port (`NATS_MONITOR_URL`), whitelisted endpoints and parameters, secrets masked; read only |
| `server/app/og.py` | og:image and sharing tags for the anonymous pages (/w, /login, /privatliv): generated images in `web_static/og/` (`node web/scripts/brand-assets.cjs`), replaced per surface or per Markdown page in /admin/web |
| `server/app/captcha.py` | proof-of-work captcha (no third party) in front of `POST /api/auth/otp/start` |
| `server/app/gdpr.py`, `reapply_erasures.py` | GDPR export (zip) and erasure; re-applying erasures after a backup restore |
| `server/tests/` | pytest against real Postgres (`TEST_DATABASE_ADMIN_URL`) |
| `web/src/` | `pages/` app · `admin-app/` the standalone /admin app (own entry `web/admin.html`, spec in `specs/admin-app/`) · `features.tsx` FeatureGate · `mobile/` /m · `extension/` /extension · `office/` /office-addin · `bridge/` postMessage clients |
| `extension/` | MV3 browser extension (side panel) |
| `deploy/` | `deploy.sh` (on the server), `compose.yml` (prod stack: Postgres, app, NATS, cloudflared), `nats.conf` (NATS + JetStream on the `nats` volume), `postgres-init.sh`, `.env.example`, `app.env.example` · `mail/` the Stalwart mail server on UpCloud (Terraform, `config.toml`, runbook) |
| `.github/workflows/` | `test.yml` (PRs), `deploy.yml` (push to main → prod) |
| `mobile/` | Expo app (iOS + Android): native login, then `/m` in a WebView — spec in `specs/mobile/` |
| `server/app/mcp/` | MCP servers: `/mcp` (appen, as the user) and `/mcp/admin` (admin, drafts) — spec in `specs/mcp/` |
| `server/app/access.py`, `mcp/toolmap.py` | the endpoint → tool map (Code and Data tools) governed from /admin/data, internal/external users, MCP access requests with approval from a list or an e-mail link — `specs/mcp/ACCESS.md` |

## Routes — one server, many surfaces
Everything is served by the one Python server on one domain. Which surface answers depends on the path.
They share the same login (session cookie), the same database and the same API.

<!-- routes:start -->
<!-- Generated from web/src/routes.json by npm run app-map --prefix web — edit the manifest, not this table. -->
| Route | Who | What | Code |
| --- | --- | --- | --- |
| `/` | logged in | The web app's home. **Anonymous visitors are redirected to `/w/`** (by the server, and by the SPA on client-side navigation). | main.py `spa()`, `web/src/pages/Home.tsx` |
| `/w/…` | everyone | **Public website**, server-rendered HTML from Markdown pages (`web_pages`), da/en via `?lang=` + `w_lang` cookie. The front page is the Markdown page with an empty slug, or — setting `web_front = "facit246"` in `/admin/web` — the fixed "Facit · 246" story (da/en/de/sv/no, browser language by default; `web_front.py`, design in `specs/forside-246/`). The front page always ends with a **login card**; on a new instance (no users, no `BOOTSTRAP_ADMIN_EMAIL`) it says the first person to log in becomes admin. A placeholder is shown until a front page is written. | `routes/web.py`, `web_front.py`, `web_static/` |
| `/login` | anonymous | E-mail → one-time code → second factor if "Kræv passkey" is on, else a passkey offer (see `gaer-auth`). Shows the first-user/admin notice when `first_user` is true. Also `/extension/login` and `/m/login` (same page, embedded layout). | `pages/Login.tsx` |
| `/klip`, `/filer`, `/konto`, `/konto/forbrug`, `/privatliv` | logged in (privatliv: everyone) | Clips, files in Object Storage, account (profile, avatar, passkeys, security keys, authenticator apps, "Kræv passkey", sessions), own AI consumption and budget (`/konto/forbrug`), privacy + consent + GDPR requests. | `pages/`, `clips.tsx` |
| `/user`, `/user/<id>` | logged in (`/user/<id>`: people the owner gave access) | **Mit område**: your dashboard (counts, recent activity in your area, areas shared with you), settings (the account page) and sharing: give another user access by e-mail as **reader** (sees overview, clips, files, downloads) or **author** (also creates/edits clips and uploads files; never deletes), optionally expiring. They open it at `/user/<id>`; settings, security and sharing stay the owner's. Everything a delegate does is audited with `on_behalf_of`. | `pages/UserArea.tsx`, `routes/user_area.py`, `delegation.py` |
| `/user/mail`, `/admin/mail` | logged in (`/admin/mail`: admin, section Drift) | **Mail, calendar and contacts** (spec in `specs/mail/`): one personal mailbox per user in **Stalwart** plus shared mailboxes (reader/sender/manager), calendars (CalDAV/JMAP) and address books (CardDAV) with sharing, all outbound mail relayed by **Lettermint**, mail kept at most **30 days**. `/user/mail` manages your mailbox, aliases, identities, app passwords, calendars, address books and client setup (`/user/<id>/mail` is the same page for your own id, 404 for anyone else); `/admin/mail` is the operator console (domains + DNS check, mailboxes, shared, calendars & contacts, sending, retention, health, audit) and `/admin/mail/users/<id>` one user's mail. Off unless `MAILBOX_BACKEND` is set. | `pages/Mail.tsx`, `admin-app/pages/mail/`, `mailbox/`, `routes/mail.py`, `routes/admin_mail.py` |
| `/forbind`, `/go` | logged in | **MCP consent** (`/forbind`: a client asks for access, `mcp:admin` needs a fresh second factor) and **deep links** (`/go?path=…&focus=…`, also `/admin/go`) that open a page and focus a `data-deeplink` anchor. | `pages/McpConsent.tsx`, `deeplink.tsx`, `admin-app/go.tsx` |
| `/develop` | logged in | **Prompt catalogue** for the development process: ready-made prompts to paste into Claude (artifacts, new features, endpoints, migrations, tests, review, debugging), filtered by category and search, copied with one click. Linked from the home page. The prompts live in the code (`web/src/develop/prompts.ts`). | `pages/Develop.tsx`, `develop/prompts.ts` |
| `/admin/…` | admin role (+ MFA) | **Standalone admin app** (own Vite entry `web/admin.html`): statistics, users + impersonation, features and rings, App Stores, auth/audit log, analytics, heatmaps, GDPR, retention, consent, files, website (`/admin/web`), AI (`/admin/ai`), AI consumption and budgets (`/admin/consumption`), extension & Office (`/admin/integrations`), MCP servers (`/admin/mcp`), webhooks (`/admin/webhooks`), jobs and cleanup (`/admin/jobs`), NATS monitoring (`/admin/nats`), mail (`/admin/mail`), data catalog + Udforsk + API sources (`/admin/data`), settings. Every read and change is audited. | `admin-app/`, `routes/admin.py`, `impersonation.py`, `features.py`, `app_store.py` |
| `/m/…` | logged in | **Mobile app (PWA)**: home, AI chat (if enabled), clips, files with camera upload, profile. Bottom tab bar, own manifest + service worker scoped to `/m/`. | `mobile/Mobile.tsx`, `public/m/` |
| `/extension/…` | logged in, inside the browser extension | **Side panel** of the Chrome/Edge extension (iframe). Current page + per-domain consent + save as clip, AI chat with the page as context, clips, account. Talks to the panel over `gaer-bridge`. | `extension/`, `web/src/extension/`, `bridge/extension.ts` |
| `/office-addin?app=word\|excel\|powerpoint\|outlook` | public shell | **Office router**: loads Office.js, detects the host, embeds the matching tool. Must stay public (Office closes panes that do not initialise). | `office/OfficeShell.tsx`, `office/handlers.ts` |
| `/extension/tools/word\|outlook\|document` | logged in, inside the Office shell | Office tools: insert/save clips and AI chat with the document or mail as context; Office.js calls go to the shell over `gaer-office-host`. | `office/Tools.tsx`, `bridge/officeHost.ts` |
| `/api/…` | per endpoint | JSON API. State-changing calls need the `X-Gaer: 1` header (CSRF). Groups: `auth`, `me`, `files`, `clips`, `bridge-consent`, `ai`, `public`, `consent`, `collect`, `office`, `extension/download`, `admin`. | `routes/` |
| `/mcp`, `/mcp/admin` | MCP clients (OAuth bearer) | **MCP servers** (Streamable HTTP): the app as the logged-in user, and admin where every change is a draft. Consent page `/forbind`; deep links `/go` and `/admin/go`; hub in `/admin/mcp`. OAuth: `/.well-known/oauth-*`, `/mcp/authorize`, `/mcp/token`, `/mcp/register`. | `server/app/mcp/`, skill `gaer-mcp` |
| `/health` | deploy | Database check + version; used by `deploy/deploy.sh` before switching versions. | `main.py` |

<details><summary>All routes (77)</summary>

| Path | Served by | Access | Page |
| --- | --- | --- | --- |
| `/` | app (`index.html`) | logged in | Overblik |
| `/w/{slug}` | server | everyone | Website |
| `/login` | app (`index.html`) | everyone | Log ind · embeddable |
| `/extension/login` | app (`index.html`) | everyone | Log ind i udvidelsen · own chrome · embeddable |
| `/m/login` | app (`index.html`) | everyone | Log ind på mobil · own chrome |
| `/klip` | app (`index.html`) | logged in | Klip (feature `klip`) |
| `/klip/:id` | app (`index.html`) | logged in | Klip (feature `klip`) |
| `/filer` | app (`index.html`) | logged in | Filer (feature `filer`) |
| `/konto` | app (`index.html`) | logged in | Konto |
| `/user` | app (`index.html`) | logged in | Mit område |
| `/user/indstillinger` | app (`index.html`) | logged in | Indstillinger |
| `/user/deling` | app (`index.html`) | logged in | Deling |
| `/user/mail` | app (`index.html`) | logged in | Mail (feature `mail`) |
| `/user/:id` | app (`index.html`) | logged in | Delt område |
| `/user/:id/mail` | app (`index.html`) | logged in | Mail (feature `mail`) |
| `/konto/forbrug` | app (`index.html`) | logged in | Mit AI-forbrug |
| `/privatliv` | app (`index.html`) | everyone | Privatliv |
| `/forbind` | app (`index.html`) | logged in | Forbind MCP-klient |
| `/godkend` | app (`index.html`) | logged in | Godkend adgang |
| `/go` | app (`index.html`) | everyone | Deep link |
| `/develop` | app (`index.html`) | logged in | Udvikling |
| `/admin` | admin app (`admin.html`) | admin | Statistik |
| `/admin/go` | admin app (`admin.html`) | admin | Deep link |
| `/admin/users` | admin app (`admin.html`) | admin | Brugere og roller |
| `/admin/impersonation` | admin app (`admin.html`) | admin | Efterligning |
| `/admin/features` | admin app (`admin.html`) | admin | Features og ringe |
| `/admin/app-stores` | admin app (`admin.html`) | admin | App Stores |
| `/admin/app-stores/:tab` | admin app (`admin.html`) | admin | App Stores |
| `/admin/analytics` | admin app (`admin.html`) | admin | Analytics |
| `/admin/heatmaps` | admin app (`admin.html`) | admin | Heatmaps |
| `/admin/logs/auth` | admin app (`admin.html`) | admin | Auth-log |
| `/admin/logs/audit` | admin app (`admin.html`) | admin | Audit-log |
| `/admin/web` | admin app (`admin.html`) | admin | Website |
| `/admin/files` | admin app (`admin.html`) | admin | Filer |
| `/admin/knowledge` | admin app (`admin.html`) | admin | Viden |
| `/admin/knowledge/spaces/:id` | admin app (`admin.html`) | admin | Vidensrum |
| `/admin/knowledge/agents/:id` | admin app (`admin.html`) | admin | Agent |
| `/admin/ai` | admin app (`admin.html`) | admin | AI |
| `/admin/consumption` | admin app (`admin.html`) | admin | AI-forbrug |
| `/admin/integrations` | admin app (`admin.html`) | admin | Udvidelse og Office |
| `/admin/mcp` | admin app (`admin.html`) | admin | MCP-servere |
| `/admin/webhooks` | admin app (`admin.html`) | admin | Webhooks |
| `/admin/jobs` | admin app (`admin.html`) | admin | Jobs og oprydning |
| `/admin/nats` | admin app (`admin.html`) | admin | NATS |
| `/admin/mail` | admin app (`admin.html`) | admin | Mail |
| `/admin/mail/users/:id` | admin app (`admin.html`) | admin | Brugerens mail |
| `/admin/data` | admin app (`admin.html`) | admin | Katalog |
| `/admin/data/explore` | admin app (`admin.html`) | admin | Udforsk |
| `/admin/data/connect` | admin app (`admin.html`) | admin | Forbind API |
| `/admin/data/tools` | admin app (`admin.html`) | admin | MCP-værktøjer |
| `/admin/data/:id` | admin app (`admin.html`) | admin | Datasæt |
| `/admin/gdpr` | admin app (`admin.html`) | admin | GDPR-forespørgsler |
| `/admin/access` | admin app (`admin.html`) | admin | Adgangsanmodninger |
| `/admin/retention` | admin app (`admin.html`) | admin | Sletning af logdata |
| `/admin/consent` | admin app (`admin.html`) | admin | Samtykke og cookies |
| `/admin/settings` | admin app (`admin.html`) | admin | Indstillinger |
| `/m` | app (`index.html`) | logged in | Hjem · own chrome |
| `/m/klip` | app (`index.html`) | logged in | Klip · own chrome |
| `/m/chat` | app (`index.html`) | logged in | AI · own chrome |
| `/m/filer` | app (`index.html`) | logged in | Filer · own chrome |
| `/m/profil` | app (`index.html`) | logged in | Profil · own chrome |
| `/extension` | app (`index.html`) | logged in | Aktuel side · own chrome · embeddable |
| `/extension/chat` | app (`index.html`) | logged in | AI · own chrome · embeddable |
| `/extension/klip` | app (`index.html`) | logged in | Klip · own chrome · embeddable |
| `/extension/konto` | app (`index.html`) | logged in | Konto · own chrome · embeddable |
| `/extension/agent` | app (`index.html`) | logged in | Agenter (feature `knowledge`) · own chrome · embeddable |
| `/extension/agent/:slug` | app (`index.html`) | logged in | Agent (feature `knowledge`) · own chrome · embeddable |
| `/office-addin` | app (`index.html`) | everyone | Office-router · own chrome · embeddable |
| `/extension/tools` | app (`index.html`) | logged in | Office-værktøjer · own chrome · embeddable |
| `/extension/tools/word` | app (`index.html`) | logged in | Word · own chrome · embeddable |
| `/extension/tools/outlook` | app (`index.html`) | logged in | Outlook · own chrome · embeddable |
| `/extension/tools/document` | app (`index.html`) | logged in | Dokument · own chrome · embeddable |
| `/api/{path}` | server | everyone | API · embeddable |
| `/mcp` | server | OAuth bearer | MCP (appen) |
| `/mcp/admin` | server | OAuth bearer | MCP (admin) |
| `/.well-known/{name}` | server | everyone | OAuth-metadata |
| `/health` | server | everyone | Sundhedstjek |

</details>
<!-- routes:end -->

Rules for routes:
- **`web/src/routes.json` describes every route** — path, which app answers (`app`, `admin`, `server`), access
  (`public`, `user`, `admin`, `token`), `chrome: "own"`, `embeddable`, `feature`, a Danish title and a group (one row in
  the tables above, with English and Danish text). Describe a route there, nowhere else.
- Adding a page: add it to `routes.json`, add the `<Route>` in `web/src/App.tsx` (or `web/src/admin-app/main.tsx` +
  `nav.ts`, where the label must equal the title), then run `npm run app-map --prefix web`. That writes
  `server/app/routes.json`, `server/app/mcp/app_map.json` and the route tables in this file and `README.md`.
  `npm run build` fails when a `<Route>` and the manifest disagree or anything generated is out of date.
- The server reads the manifest (`app/route_manifest.py`): `EMBEDDABLE` (CSP frame-ancestors for the extension and
  Office hosts) and which HTML file answers. The app on / reads `chrome: "own"` for pages without the top bar.
  Mark a route `embeddable` only if it really must run inside them.
- `/admin` is a separate app: it shares only `api.ts`, `auth.tsx`, `bridge/`, `deeplink.tsx` and `routes.json` with the
  rest of `web/src/`. Neither side imports the other; `npm run build` fails if one does (`appBoundary()` in
  `web/vite.config.ts`).
- Anything the public may see without login lives under `/w/`, `/login`, `/privatliv` or `/api/public/…`.
- AI is optional: UI for it must hide itself when `/api/ai/status` says `enabled: false`.

## Rules that always apply
1. **Login rules are fixed** (skill `gaer-auth`). Never add a way around the code, the second factor, the admin grace
   logins or the step-up for admin security actions.
2. **Every data change is audited** in the same transaction (skill `gaer-audit`). Logs are append-only; only the
   purge database user deletes, and the deletion is logged.
3. **Admin = admin role** plus one or more **admin roles** (Fuld adgang, Support, Indhold, Produkt, Data, Privatliv,
   Drift) that decide which parts of /admin an admin may use (`server/app/admin_roles.py`, skill `gaer-auth`).
   Everything under `/api/admin` is guarded and audited; a new /admin section must be added to `admin_roles.SECTIONS`.
4. **Personal data** goes into the GDPR export and is removed on erasure — both live in `server/app/gdpr.py`,
   used by `/admin/gdpr` and by users themselves (`/api/me/export`, `/api/me/delete`). A new table with personal
   data is not done until it is in both. Analytics only identify a visitor after consent. Nothing is sent to
   third parties.
5. **Migrations are new files**; never edit an applied one.
6. **Background work goes through `app.jobs`** (never a loose `asyncio.create_task`), so it is retried, visible in
   /admin/jobs and safe with several containers. Data that grows gets a row in `retention_policies` with its SQL in
   `retention.CLEANUP_SQL`. New webhook events go in `webhooks.EVENTS` and carry ids, not personal data. What an incoming webhook may do is
   a handler in the code (`webhooks_in.HANDLERS`), never configuration.
7. **No secrets in the repo.** Configuration comes from environment variables (`server/app/config.py`).
8. Before you say you are done: `pytest server` and `npm run build --prefix web` pass.
9. UI text is Danish.

## Workflows (slash commands)
`/gaer-boot` read in · `/gaer-init` new project · `/gaer-dev` run locally · `/gaer-db` migrations ·
`/gaer-mix` mix in a recipe · `/gaer-new-feature` · `/gaer-pr` · `/gaer-pr-review` · `/gaer-release` ·
`/gaer-recover` · `/gaer-whatsup`. Source: `.gaer/agents/workflows/`.

## Skills
`gaer-backend`, `gaer-auth`, `gaer-audit`, `gaer-python-api`, `gaer-vite`, `gaer-profile`, `gaer-storage`, `gaer-integrations`, `gaer-mcp`.
Source: `.gaer/agents/skills/*/SKILL.md`.
