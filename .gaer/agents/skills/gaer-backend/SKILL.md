---
name: gaer-backend
description: What already exists in the Gær backend — login, the container's own Postgres database and the Python setup. Read this before you add a server, a database, an auth library or a new language runtime. Use for any task that touches server/, data, users or sign-in.
---

# The backend that is already there (gaer-backend)

Gær ships with a working backend. **Do not add another one.** No Node/Express API, no Supabase, Firebase,
Prisma, SQLAlchemy, Auth0, NextAuth, Clerk or SQLite. Build on what is below.

## 1. Login is solved
The Python server already handles sign-in, sessions and roles. Rules and details: skill `gaer-auth`.

| What | Where |
| --- | --- |
| Phone number → 6-digit code by SMS → session | `POST /api/auth/otp/start`, `POST /api/auth/otp/verify` (`routes/auth.py`) |
| Passkeys (WebAuthn) after three grace logins | `/api/auth/passkey/register/*`, `/api/auth/passkey/login/*` |
| Who is signed in | `GET /api/auth/me` · frontend: `useAuth()` in `web/src/auth.tsx` |
| Session cookie `gaer_session` (only the SHA-256 is stored) | `server/app/sessions.py` |
| Roles: `user` and `admin`. The first user becomes admin | `users.role` |

Guard every endpoint with a dependency from `app.sessions`:

```python
from fastapi import APIRouter, Depends
from app import db
from app.logs import audit
from app.sessions import Principal, current_user, require_admin

router = APIRouter(prefix="/api/notes")

@router.get("")
async def list_notes(user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        return await db.fetch_all(conn, "select id, body from notes where user_id = %s order by id desc", (user.user_id,))
```

- `current_user` — a full session (the default for everything).
- `require_admin` — full session + `admin` role; GETs are logged as `admin.read`. Use it for `/api/admin/*`.
- `any_session` — also accepts an enroll session. **Only** for passkey registration.
- State-changing requests must send `X-Gaer: 1` (CSRF). The frontend `api` client in `web/src/api.ts` does it.
- Locally, `SMS_PROVIDER=console` prints the code in the server log and shows it on the login page.
  Never add a "dev login" or a bypass — use the console provider.

## 2. The container has its own Postgres database
Each container gets its **own database** (`gaer_<customer>`). Locally it runs in Docker
(`docker compose up -d` → `postgres:16` on `localhost:5432`, user/password/db `gaer`). In production it
is a database on the managed PostgreSQL cluster on OVHcloud that only this container can reach.

- Driver: **psycopg 3**, async pool, **raw SQL**, rows as dicts. No ORM. Code: `server/app/db.py`.
- `async with db.connection() as conn:` is one transaction — committed on success, rolled back on error.
  Use `db.fetch_one` / `db.fetch_all` with `%s` parameters. Never format input into SQL.
- Schema = plain SQL files in `server/app/migrations/NNNN_name.sql`, applied by `python -m app.migrate`
  (`--status` lists them). New change → new file; never edit an applied one. Workflow: `/gaer-db`.
- Three database users, one per job:

  | Env | User | May |
  | --- | --- | --- |
  | `DATABASE_URL` | `<db>_app` | the app. SELECT/INSERT/UPDATE/DELETE, but only SELECT/INSERT on log tables |
  | `MIGRATION_DATABASE_URL` | `<db>_owner` | owns the schema, runs migrations |
  | `PURGE_DATABASE_URL` | `<db>_purge` | deletes log data, from /admin only (`db.purge_connection()`) |

- `gaer_apply_grants()` runs after every migration, so new tables get grants automatically.
- Existing tables include `users`, `sessions`, `otp_codes`, `passkeys`, `auth_log`, `audit_log`,
  `files`, `clips`, `settings`, `consents`, `analytics_events`, `gdpr_requests`, `bridge_consents`.
  Read `0001_init.sql` before you create a table that might already exist.
- Every change writes `audit()` in the same transaction (skill `gaer-audit`). Personal data must be in the
  GDPR export (`build_user_export`) and removed on erasure.
- Files go to Object Storage, not the database (skill `gaer-storage`).

## 3. Python setup
- Python **3.12+** (the image uses 3.13). Code in `server/`, package `app`, settings in `server/pyproject.toml`.
- FastAPI + uvicorn. Dependencies are pinned in `server/requirements.txt`; dev/test extras in
  `server/requirements-dev.txt`. Add a dependency there, pinned, and only when the standard library or an
  existing one cannot do it.
- Virtual environment at the repo root:
  ```bash
  python -m venv .venv && . .venv/bin/activate
  pip install -r server/requirements-dev.txt
  ```
- Configuration only from environment variables in `server/app/config.py`; read as `config.settings.x`.
  Locally they come from `.env` (copy `.env.example`). No secrets in the repo.
- Run: `cd server && uvicorn app.main:app --reload --port 8000 --env-file ../.env`. Vite on :5173 proxies
  `/api` and `/health` to it. In production the same server also serves `web/dist`. Workflow: `/gaer-dev`.
- One router per area: `server/app/routes/<area>.py` with `APIRouter(prefix="/api/<area>")`, registered in
  `main.py`. Validate input with Pydantic models. Errors: `HTTPException(status, "Dansk besked")`.
- Tests: pytest against a **real Postgres** with the production role layout (`server/tests/conftest.py`):
  ```bash
  TEST_DATABASE_ADMIN_URL=postgresql://gaer:gaer@localhost:5432/gaer pytest server
  ```
  Fixtures `client` / `make_client`, helpers `otp_login`, `full_user` in `tests/helpers.py`.

## Checklist before you say done
- [ ] No new backend, database, ORM or auth library was added.
- [ ] Endpoints use `current_user` / `require_admin`; user data is scoped by `user_id`.
- [ ] Schema changes are a new migration file; `python -m app.migrate --status` is clean.
- [ ] `audit()` on every change; GDPR export and erasure cover new personal data.
- [ ] `pytest server` and `npm run build --prefix web` pass.
