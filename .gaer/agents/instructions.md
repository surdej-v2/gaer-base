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
| `server/app/routes/` | `auth` login + second factor · `me` profile, avatar, account · `security` own factors + "Kræv passkey" · `files` own home + /shared · `admin_files` /api/admin/files tree · `clips` + bridge consent · `public` config, consent, analytics, GDPR form · `admin` /api/admin · `impersonation` test accounts · `features` rings + usage · `admin_ops` /api/admin webhooks (out and in), jobs, retention policies · `hooks` incoming webhooks · `review` review panel (notes, sessions, zip) · `app_store` App Stores · `data` /api/admin/data catalog, Udforsk, API sources · `nats` /api/admin/nats monitoring · `office` manifests · `web` /w website + extension zip · `user_area` /api/user Mit område + delegated access · `meta` /api/meta trees + `admin_meta` /api/admin/meta · `mail` /api/mail + `admin_mail` /api/admin/mail (mail module) · `messages` /api/messages inbox, replies, preferences + `admin_messages` /api/admin/messages (Beskeder) |
| `server/app/sessions.py` | session cookie, `Principal`, `current_user` / `require_admin` / `require_admin_stepup` / `any_session` |
| `server/app/mfa.py`, `totp.py` | second-factor rules (who needs what, changing factors, `stepup_ok` incl. the YubiKey requirement), authenticator apps (TOTP) |
| `server/app/signup.py`, `routes/admin_users.py` | who may create an account by logging in (setting `signup`: open, domains, closed); admins create users, save the full user edit in one transaction, send a login link ("Gendan adgang") |
| `server/app/yubikey.py`, `routes/yubikey.py` | the YubiKey requirement (`users.yubikey_required`): is a key a YubiKey by its attestation (`YUBICO_ROOTS`), enrolment by an admin in person or with a mailed link (`/konto#yubikey=…`) |
| `server/app/filetree.py` | file tree over `files`: a home per user under `/users/<id>/` (`~/` = your own) and `/shared/` for project files; folders (`file_folders`), access rules, upload/write/move/delete with audit — used by `/api/files`, `/api/admin/files`, the AI chat and both MCP servers |
| `server/app/uploads.py` | Direct upload to Object Storage: the browser PUTs to presigned links (multipart for big files) past Cloudflare's 100 MB limit, then completes (`/api/files/uploads`, `/api/admin/files/uploads`; client `web/src/upload.ts`). A zip is kept as it is or unpacked into the tree by the job `files.unzip` (folders kept, limits `UNZIP_MAX_*`); jobs `files.uploads_cleanup`, `storage.cors` (bucket CORS for `ORIGIN`) |
| `server/app/mailbox/` | Mail, calendar and contacts (`specs/mail/`): `backend.py` Stalwart client (or in memory, `MAILBOX_BACKEND`), `service.py` rules (provisioning, shared-mailbox and collection roles, identities, app passwords, 30-day retention), `dns.py` domain records + live check, `tasks.py` jobs `mail.*` and the `lettermint` incoming-webhook handler |
| `server/app/messages/` | Beskeder (`specs/beskeder/`): `service.py` audiences, the estimate, the two-step confirmation token, fan-out of a broadcast (an in-app row per recipient, plus SMS with the user's own yes or e-mail), quiet hours, reply threads; `tasks.py` jobs `messages.deliver`, `messages.sms` (duplicates, hourly cap, retries), `messages.mail`; `text.py` SMS segments (GSM-7/UCS-2); `chat.py` (phase 2) Fællessamtalen and direct conversations, blocks, the word filter, reports and moderation |
| `server/app/meta.py` | Træstrukturer (`specs/meta/`): dimensions and nodes with materialised paths (`/org/drift/support`, private `~/…` per user), who may create where (top level: admin role Indhold; locked dimension: admin; below: everyone), search, resolve stored ids, subtree expansion, move with path rewrite. UI: `web/src/tree/` (`TreeView`, `TreeSelect`, `useTree`), examples on `/developer/trae`, admin on `/admin/meta`. Other tables store node ids, never paths. `server/app/meta_objects.py`: tagging objects (URL, prefix pattern `…/*`, host, entity `type:id`, note) with nodes across dimensions, visibility through the nodes, the context of a node (parents, siblings, children, objects, related) and URL look-up for the extension; `TagEditor` in `web/src/tree/` |
| `server/app/groups.py`, `routes/groups.py` | Groups (a general feature): `/api/admin/groups` (section `/groups`, role Support) and `/api/me/groups`; members are `member` or `manager`; adding a member needs a step-up (it can give access to secrets) |
| `server/app/secret_store.py`, `routes/admin_secrets.py` | Secret manager (`/admin/secrets`, role Drift): a secret is owned by a group and has its own RACI (one A, ≥1 R; default creator A+R, rest of the group I); values encrypted with `secrets_box`, versioned, never logged/mailed/in webhooks or MCP; access only by a request approved by A, R and C (rule per secret), always expiring; reveal/change/approve/RACI need a step-up; notifications per group (`group_notify_rules`, jobs `secrets.mail`, `secrets.digest`, `secrets.maintenance`); API sources get the level `use` ("Brug i kilde") through the same approval, and `server/app/source_secrets.py` resolves a kildebeskrivelse's `hemmelighed: gruppe/navn` for a call (never a value in the description, the browser or logs) |
| `server/app/integrations.py` | APIs called from code (AI, Lettermint and the other mail providers, OVH SMS, Stalwart, NATS monitoring, Object Storage): what each is for, which files call it, which settings hold its keys, its endpoints. Their httpx clients get `integrations.hooks(key)`, which records each call as the event `api.call` (ref = integration + endpoint pattern only, never a real path); API sources are recorded the same way. Shown in /admin/data/explore → Forbindelser (`GET /api/admin/data/connections`). A new outside API is not done until it is in the registry |
| `server/app/delegation.py` | Mit område (`/user`): sharing your area with another user as reader or author (`/user/<id>`), access checks, the activity list; delegates' changes are audited with `on_behalf_of` |
| `server/app/db.py` | connection pools: `db.connection()`, `db.purge_connection()`, `fetch_one`, `fetch_all` |
| `server/app/config.py` | all settings from environment variables |
| `server/app/migrations/` | plain SQL, applied by `python -m app.migrate` |
| `server/app/ai.py`, `routes/ai.py` | optional AI chat (OVHcloud AI Endpoints), client tools, usage + monthly limits |
| `server/app/consumption.py`, `routes/consumption.py` | AI consumption: what limit applies (budget → own limit → default), budgets per user (monthly cap, warning levels, hard/soft, optional period total, start/end date), alerts (`ai_budget_alerts`, webhooks `ai.budget.*`), reports per period/model/user for `/admin/consumption`, the user's own page `/konto/forbrug` (`GET /api/ai/usage`) |
| `server/app/adoption.py`, `adoption_match.py`, `routes/adoption.py` | adoption journeys (`/admin/adoption`): signed-in page visits (`adoption_visits`, only while the setting `adoption_tracking` is on), steps matched exact/prefix/glob/regex, sequential funnel, forecast, cohorts, nudges as webhook `adoption.nudge.sent`; journey roles reader/editor/owner |
| `server/app/logs.py`, `retention.py` | auth/audit log writers, purge of logs, cleanup of operational data (`retention_policies`) |
| `server/app/jobs.py` | background jobs: queue in Postgres (`jobs`), workers + scheduler (`job_schedules`) in the app process, retries with backoff; `@jobs.handler("kind")`, `jobs.enqueue(conn, …)` |
| `server/app/webhooks.py`, `secrets_box.py` | outgoing webhooks: `webhooks.emit(conn, event, data)` in the same transaction; signed (HMAC), optional auth header, retries via jobs, SSRF guard; secrets encrypted (AES-GCM) |
| `server/app/webhooks_in.py`, `routes/hooks.py` | incoming webhooks: `POST /api/hooks/in/<key>` (CSRF-exempt), verified per endpoint (Gær signature, HMAC header, fixed header such as Authorization, or URL only) + IP filter, size and rate limits, dedupe on delivery id; stored, then `@webhooks_in.handler("name")` runs as a job |
| `server/app/events.py` | events: `await events.emit("kind", ref=…, value=…)` → NATS stream `EVENTS` (7 days) → consumer → Postgres `events` (7 days); without NATS straight to Postgres. Rules (setting `kill_rules`) close a kill switch when a kind happens too often |
| `server/app/killswitch.py` | kill switches (`SWITCHES`, code not config): a closed one answers 503 for its paths within ~2 s in every container (guard middleware); /admin, login, /health never close |
| `server/app/resources.py`, `routes/resources.py` | /admin/resources: resources in use (container, Postgres, events, Object Storage, NATS, AI, e-mail/SMS, jobs) with limits and prices (setting `resources`), plus kill switches, rules and events by kind |
| `server/app/nats_monitor.py`, `routes/nats.py` | /admin/nats: reads NATS' HTTP monitoring port (`NATS_MONITOR_URL`), whitelisted endpoints and parameters, secrets masked; read only |
| `server/app/og.py` | og:image and sharing tags for the anonymous pages (/w, /login, /privatliv): generated images in `web_static/og/` (`node web/scripts/brand-assets.cjs`), replaced per surface or per Markdown page in /admin/web |
| `server/app/license.py` | the license key from happymates.ai: `LICENSE_KEY` is a JWT (EdDSA, keys in `ISSUER_KEYS` in the code) with license id, licensee, `domains` array and statement; `LicenseHeadMiddleware` puts the statement in the `<head>` of every /w page (UNLICENSED when the key is missing or wrong), status on `/health`. Never remove or bypass it |
| `server/app/canonical_host.py` | `REDIRECT_HOSTS`: alias domains (apex, www, the fork's own name) redirect to `ORIGIN` (301/308, path kept, never /health); set by the Gær Fleet console, `deploy/k8s/CUSTOM-DOMAINS.md` |
| `server/app/captcha.py` | proof-of-work captcha (no third party) in front of `POST /api/auth/otp/start` |
| `server/app/gdpr.py`, `reapply_erasures.py` | GDPR export (zip) and erasure; re-applying erasures after a backup restore |
| `server/tests/` | pytest against real Postgres (`TEST_DATABASE_ADMIN_URL`) |
| `web/src/` | `pages/` app · `admin-app/` the standalone /admin app (own entry `web/admin.html`, spec in `specs/admin-app/`) · `features.tsx` FeatureGate · `mobile/` /m · `extension/` /extension · `office/` /office-addin · `bridge/` postMessage clients |
| `extension/` | MV3 browser extension (side panel) |
| `deploy/` | `deploy.sh` (on the server), `compose.yml` (prod stack: Postgres, app, NATS, cloudflared), `nats.conf` (NATS + JetStream on the `nats` volume), `postgres-init.sh`, `.env.example`, `app.env.example` · `k8s/` the same app on Kubernetes, one namespace per fork made by the Gær Fleet console in Surdej (`README.md`, `app.yaml`, `deploy.sh`, `cluster/`) · `mail/` the Stalwart mail server on UpCloud (Terraform, `config.toml`, runbook) |
| `.github/workflows/` | `test.yml` (PRs), `deploy.yml` (push to main → prod), `target.yml` (the only reader of the variable `DEPLOY_TARGET` = `compose` (default) / `k8s` / `none`; every deploy workflow tests its outputs), `build-image.yml` (GHCR), `deploy-k8s.yml`, `rollback.yml`, `sync-upstream.yml` (forks pull gaer-base) |
| `mobile/` | Expo app (iOS + Android): native login, then `/m` in a WebView — spec in `specs/mobile/` |
| `server/app/mcp/` | MCP servers: `/mcp` (appen, as the user) and `/mcp/admin` (admin, drafts) — spec in `specs/mcp/` |
| `server/app/routes/studio.py`, `server/app/mcp/endpoints.py` | Developer portal `/developer` (admin section `/developer`, roles Data and Fuld; `web/src/developer/`): dashboard, **API Studio** (`/developer/studio/api`, the source description as YAML → `/api/admin/data/sources`, datasets in the catalog) and **MCP Studio** (`/developer/studio/mcp`): endpoints on `/mcp/<key>` in `mcp_endpoints` (0030) with audience, instructions and the tools from the endpoint map; `transport.DynamicEndpoints` serves them, `/api/admin/studio` governs them (step-up, audited) |
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
| `/beskeder`, `/samtaler`, `/admin/messages` | logged in (`/admin/messages`: admin, sections Support, Privatliv and Drift) | **Beskeder** (spec in `specs/beskeder/`): broadcasts from the administration to an audience (everyone, admins, a feature ring, inactive users, chosen users), sent in two steps with an estimate ("X får SMS (ca. Y kr) · Z får e-mail") and a single-use confirmation token; a broadcast to everyone needs a step-up. Every message lands in the user's inbox on `/beskeder` (and `/m/beskeder`); per recipient it also goes by SMS (only with the user's own yes, never in quiet hours unless urgent; duplicates and an hourly cap) or e-mail. Recipients can reply: a private thread with the administration, answered in `/admin/messages/replies`. `/admin/messages` also has the delivery log and channels and rules. **Samtaler** (`/samtaler`, `/m/samtaler`, phase 2): Fællessamtalen (every user) and direct conversations between two users; users report messages and block each other (a block hides the other's messages and stops direct conversations both ways); a word filter rejects messages before they are stored. `/admin/messages/moderation` (Support, Privatliv) dismisses reports or removes the message, showing the three messages before it only while a report is open (audited); blocks only as counts. | `pages/Inbox.tsx`, `pages/Conversations.tsx`, `admin-app/pages/messages/`, `messages/`, `routes/messages.py`, `routes/admin_messages.py` |
| `/forbind`, `/go` | logged in | **MCP consent** (`/forbind`: a client asks for access, `mcp:admin` needs a fresh second factor) and **deep links** (`/go?path=…&focus=…`, also `/admin/go`) that open a page and focus a `data-deeplink` anchor. | `pages/McpConsent.tsx`, `deeplink.tsx`, `admin-app/go.tsx` |
| `/developer/…` | admin with the role Data or Fuld | **Developer portal**: a dashboard over API sources, the data catalog, MCP endpoints and tools, and the **Studio**. `/developer/studio/api` (**API Studio**) registers an API with a source description (YAML, or imported from a cURL command or an OpenAPI document), tests the call and saves it so its datasets land in the data catalog (`/admin/data`). `/developer/studio/mcp` (**MCP Studio**) defines MCP endpoints on `/mcp/<key>` — name, audience, instructions and which tools they offer (the app's Code tools and Data tools from the catalog) — tries a tool, shows the log and what a client needs to connect. `/developer/prompts` is the **prompt catalogue** (ready-made prompts for Claude, in `developer/promptCatalog.ts`) and `/developer/trae` shows **tree structures** (`web/src/tree/`) against the real `/api/meta`. `/develop` redirects here. | `developer/`, `server/app/routes/studio.py`, `server/app/mcp/endpoints.py`, `tree/` |
| `/admin/…` | admin role (+ MFA) | **Standalone admin app** (own Vite entry `web/admin.html`): statistics, users + impersonation, features and rings, App Stores, auth/audit log, analytics, heatmaps, GDPR, retention, consent, files, tree structures (`/admin/meta`), website (`/admin/web`), AI (`/admin/ai`), AI consumption and budgets (`/admin/consumption`), extension & Office (`/admin/integrations`), MCP servers (`/admin/mcp`), webhooks (`/admin/webhooks`), jobs and cleanup (`/admin/jobs`), NATS monitoring (`/admin/nats`), resources, kill switches and events (`/admin/resources`), mail (`/admin/mail`), secrets owned by groups with RACI and approved access (`/admin/secrets`), groups (`/admin/groups`), data catalog + Udforsk + API sources (`/admin/data`), adoption journeys measured by the routes users visit (`/admin/adoption`), settings. Every read and change is audited. | `admin-app/`, `routes/admin.py`, `impersonation.py`, `features.py`, `app_store.py`, `admin-app/pages/Meta.tsx` |
| `/m/…` | logged in | **Mobile app (PWA)**: home, AI chat (if enabled), clips, files with camera upload, profile. Bottom tab bar, own manifest + service worker scoped to `/m/`. | `mobile/Mobile.tsx`, `public/m/` |
| `/extension/…` | logged in, inside the browser extension | **Side panel** of the Chrome/Edge extension (iframe). Current page + per-domain consent + save as clip, AI chat with the page as context, clips, **tags** for the page (`/extension/meta`: what it is tagged with, tag it as the page, a folder pattern or the whole domain, browse a tag's context; the address is only looked up automatically on allowed domains), account. Talks to the panel over `gaer-bridge`. | `extension/`, `web/src/extension/`, `bridge/extension.ts`, `web/src/extension/ExtensionMeta.tsx` |
| `/office-addin?app=word\|excel\|powerpoint\|outlook` | public shell | **Office router**: loads Office.js, detects the host, embeds the matching tool. Must stay public (Office closes panes that do not initialise). | `office/OfficeShell.tsx`, `office/handlers.ts` |
| `/extension/tools/word\|outlook\|document` | logged in, inside the Office shell | Office tools: insert/save clips and AI chat with the document or mail as context; Office.js calls go to the shell over `gaer-office-host`. | `office/Tools.tsx`, `bridge/officeHost.ts` |
| `/api/…` | per endpoint | JSON API. State-changing calls need the `X-Gaer: 1` header (CSRF). Groups: `auth`, `me`, `files`, `clips`, `bridge-consent`, `ai`, `public`, `consent`, `collect`, `office`, `extension/download`, `admin`. | `routes/` |
| `/mcp`, `/mcp/admin`, `/mcp/<key>` | MCP clients (OAuth bearer) | **MCP servers** (Streamable HTTP): the app as the logged-in user, admin where every change is a draft, and the endpoints defined in `/developer/studio/mcp` on `/mcp/<key>` (as the user, with the tools chosen there). Consent page `/forbind`; deep links `/go` and `/admin/go`; hub in `/admin/mcp`. OAuth: `/.well-known/oauth-*`, `/mcp/authorize`, `/mcp/token`, `/mcp/register`. | `server/app/mcp/`, skill `gaer-mcp`, `server/app/mcp/endpoints.py` |
| `/health` | deploy | Database check + version; used by `deploy/deploy.sh` before switching versions. | `main.py` |

<details><summary>All routes (141)</summary>

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
| `/beskeder` | app (`index.html`) | logged in | Beskeder (feature `beskeder`) |
| `/beskeder/:id` | app (`index.html`) | logged in | Besked (feature `beskeder`) |
| `/samtaler` | app (`index.html`) | logged in | Samtaler (feature `samtaler`) |
| `/samtaler/:id` | app (`index.html`) | logged in | Samtale (feature `samtaler`) |
| `/privatliv` | app (`index.html`) | everyone | Privatliv |
| `/forbind` | app (`index.html`) | logged in | Forbind MCP-klient |
| `/godkend` | app (`index.html`) | logged in | Godkend adgang |
| `/go` | app (`index.html`) | everyone | Deep link |
| `/developer` | app (`index.html`) | admin | Developer · own chrome |
| `/developer/studio` | app (`index.html`) | admin | Studio |
| `/developer/studio/api` | app (`index.html`) | admin | API Studio |
| `/developer/studio/mcp` | app (`index.html`) | admin | MCP Studio |
| `/developer/prompts` | app (`index.html`) | admin | Promptkatalog |
| `/developer/trae` | app (`index.html`) | admin | Træstrukturer |
| `/developer/datastudie` | app (`index.html`) | admin | Endpoints i Datastudie |
| `/developer/pipeline` | app (`index.html`) | admin | Pipelines |
| `/developer/api` | app (`index.html`) | admin | API-studie |
| `/developer/forbindelser` | app (`index.html`) | admin | Dataforbindelser |
| `/developer/eksport` | app (`index.html`) | admin | Eksport og import |
| `/developer/katalog` | app (`index.html`) | admin | Datakatalog |
| `/developer/katalog/lineage` | app (`index.html`) | admin | Lineage |
| `/developer/katalog/:id` | app (`index.html`) | admin | Datasæt |
| `/developer/godkendelser` | app (`index.html`) | admin | Venter på mig |
| `/developer/godkendelser/historik` | app (`index.html`) | admin | Godkendelseshistorik |
| `/developer/godkendelser/:id` | app (`index.html`) | admin | Godkendelse |
| `/developer/artefakter/:id` | app (`index.html`) | admin | Artefakt |
| `/developer/hjaelp` | app (`index.html`) | admin | Om Developer |
| `/developer/pipeline/ny` | app (`index.html`) | admin | Ny pipeline |
| `/developer/pipeline/snapshots` | app (`index.html`) | admin | Snapshots |
| `/developer/pipeline/koersler` | app (`index.html`) | admin | Kørsler |
| `/developer/pipeline/koersler/:id` | app (`index.html`) | admin | Kørsel |
| `/developer/pipeline/planer` | app (`index.html`) | admin | Planer |
| `/developer/pipeline/:id` | app (`index.html`) | admin | Pipeline-editor |
| `/developer/forbindelser/cache` | app (`index.html`) | admin | Cache |
| `/developer/forbindelser/:id` | app (`index.html`) | admin | Forbindelse |
| `/developer/datastudie/ny` | app (`index.html`) | admin | Nyt endpoint |
| `/developer/datastudie/endpoints/:id` | app (`index.html`) | admin | Endpoint |
| `/developer/datastudie/operationer` | app (`index.html`) | admin | Operationer |
| `/developer/datastudie/operationer/:id` | app (`index.html`) | admin | Operation |
| `/developer/datastudie/multisource` | app (`index.html`) | admin | Multisource-kilder |
| `/developer/mcp` | app (`index.html`) | admin | MCP-studie |
| `/developer/mcp/testbaenk` | app (`index.html`) | admin | Testbænk |
| `/developer/mcp/klienter` | app (`index.html`) | admin | Forbundne klienter |
| `/developer/mcp/drift` | app (`index.html`) | admin | MCP-overvågning |
| `/developer/mcp/eksterne` | app (`index.html`) | admin | Eksterne MCP-servere |
| `/admin` | admin app (`admin.html`) | admin | Statistik |
| `/admin/go` | admin app (`admin.html`) | admin | Deep link |
| `/admin/users` | admin app (`admin.html`) | admin | Brugere og roller |
| `/admin/impersonation` | admin app (`admin.html`) | admin | Efterligning |
| `/admin/features` | admin app (`admin.html`) | admin | Features og ringe |
| `/admin/app-stores` | admin app (`admin.html`) | admin | App Stores |
| `/admin/app-stores/:tab` | admin app (`admin.html`) | admin | App Stores |
| `/admin/analytics` | admin app (`admin.html`) | admin | Analytics |
| `/admin/heatmaps` | admin app (`admin.html`) | admin | Heatmaps |
| `/admin/adoption` | admin app (`admin.html`) | admin | Adoption |
| `/admin/adoption/journeys` | admin app (`admin.html`) | admin | Forløb |
| `/admin/adoption/journeys/:id` | admin app (`admin.html`) | admin | Forløb |
| `/admin/adoption/journeys/:id/setup` | admin app (`admin.html`) | admin | Opsætning af forløb |
| `/admin/adoption/routes` | admin app (`admin.html`) | admin | Ruter og URL'er |
| `/admin/adoption/console` | admin app (`admin.html`) | admin | Live-strøm |
| `/admin/adoption/access` | admin app (`admin.html`) | admin | Adgang til forløb |
| `/admin/logs/auth` | admin app (`admin.html`) | admin | Auth-log |
| `/admin/logs/audit` | admin app (`admin.html`) | admin | Audit-log |
| `/admin/web` | admin app (`admin.html`) | admin | Website |
| `/admin/files` | admin app (`admin.html`) | admin | Filer |
| `/admin/knowledge` | admin app (`admin.html`) | admin | Viden |
| `/admin/knowledge/spaces/:id` | admin app (`admin.html`) | admin | Vidensrum |
| `/admin/knowledge/agents/:id` | admin app (`admin.html`) | admin | Agent |
| `/admin/meta` | admin app (`admin.html`) | admin | Træstrukturer |
| `/admin/ai` | admin app (`admin.html`) | admin | AI |
| `/admin/consumption` | admin app (`admin.html`) | admin | AI-forbrug |
| `/admin/integrations` | admin app (`admin.html`) | admin | Udvidelse og Office |
| `/admin/mcp` | admin app (`admin.html`) | admin | MCP-servere |
| `/admin/webhooks` | admin app (`admin.html`) | admin | Webhooks |
| `/admin/jobs` | admin app (`admin.html`) | admin | Jobs og oprydning |
| `/admin/nats` | admin app (`admin.html`) | admin | NATS |
| `/admin/resources` | admin app (`admin.html`) | admin | Ressourcer |
| `/admin/mail` | admin app (`admin.html`) | admin | Mail |
| `/admin/mail/users/:id` | admin app (`admin.html`) | admin | Brugerens mail |
| `/admin/secrets` | admin app (`admin.html`) | admin | Hemmeligheder |
| `/admin/secrets/:id` | admin app (`admin.html`) | admin | Hemmelighed |
| `/admin/groups` | admin app (`admin.html`) | admin | Grupper |
| `/admin/messages` | admin app (`admin.html`) | admin | Beskeder |
| `/admin/messages/send` | admin app (`admin.html`) | admin | Send besked |
| `/admin/messages/replies` | admin app (`admin.html`) | admin | Svar |
| `/admin/messages/moderation` | admin app (`admin.html`) | admin | Moderering |
| `/admin/messages/delivery` | admin app (`admin.html`) | admin | Levering |
| `/admin/messages/settings` | admin app (`admin.html`) | admin | Kanaler og regler |
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
| `/m/beskeder` | app (`index.html`) | logged in | Beskeder (feature `beskeder`) · own chrome |
| `/m/beskeder/:id` | app (`index.html`) | logged in | Besked (feature `beskeder`) · own chrome |
| `/m/samtaler` | app (`index.html`) | logged in | Samtaler (feature `samtaler`) · own chrome |
| `/m/samtaler/:id` | app (`index.html`) | logged in | Samtale (feature `samtaler`) · own chrome |
| `/m/profil` | app (`index.html`) | logged in | Profil · own chrome |
| `/extension` | app (`index.html`) | logged in | Aktuel side · own chrome · embeddable |
| `/extension/chat` | app (`index.html`) | logged in | AI · own chrome · embeddable |
| `/extension/klip` | app (`index.html`) | logged in | Klip · own chrome · embeddable |
| `/extension/meta` | app (`index.html`) | logged in | Tags · own chrome · embeddable |
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
| `/mcp/{key}` | server | OAuth bearer | MCP (eget endpoint fra MCP Studio) |
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
- `/admin` is a separate app: it shares only `api.ts`, `auth.tsx`, `bridge/`, `deeplink.tsx`, `routes.json`, `upload.ts` and `tree/` with the
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
   /admin/jobs and safe with several containers. The only other long-running loops are `app.events` (NATS consumer and
   rule check), started and stopped by the lifespan like the job workers. Things worth counting are `events.emit(…)`:
   a kind like `area.what`, ref = an object id or route, never personal data (no user ids, e-mails, phones, IPs). Data that grows gets a row in `retention_policies` with its SQL in
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
