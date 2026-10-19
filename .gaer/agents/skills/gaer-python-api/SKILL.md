---
name: gaer-python-api
description: Conventions for endpoints, validation and errors in the Gær Python server (FastAPI + psycopg). Use when adding or changing anything in server/.
---

# Python server (gaer-python-api)

Stack: FastAPI, psycopg 3 (async pool, raw SQL, dict rows), py_webauthn, boto3 for Object Storage. No ORM.

## Layout
- `app/main.py` — app factory, CSRF/security-header middleware, per-path CSP, routers, SPA fallback, `/health`.
- `app/routes/<area>.py` — one `APIRouter(prefix="/api/<area>")` per area; register it in `main.py`.
- `app/migrations/NNNN_*.sql` — schema (see `/gaer-db`).
- `app/config.py` — all settings from environment variables; read as `config.settings.x` (not imported values).

## Endpoints
- Guard with dependencies: `current_user` (default), `require_admin` (admin routes are already guarded at
  router level in `routes/admin.py`), `any_session` only for passkey registration.
- Validate input with Pydantic models (`Field(max_length=...)`, `pattern=...`). Limit list sizes (`limit` ≤ 500).
- Database work: `async with db.connection() as conn:` — one transaction, committed on success.
  Use `db.fetch_one/fetch_all` and `%s` parameters. Never format user input into SQL.
- Always scope user data by owner: `where id = %s and user_id = %s`. Return 404 (not 403) for others' rows.
- Errors: `HTTPException(status, "Dansk besked til brugeren")`. If you must log a failure before raising,
  `await conn.commit()` first, or the rollback removes the log line.
- Write `audit()` for every change (see `gaer-audit`).
- Personal data: add it to `build_user_export` and `erase_user` in `app/gdpr.py`.

## Tests
`server/tests/` against a real Postgres with the production role layout. Use the `make_client` / `client`
fixtures and helpers `otp_login`, `full_user`. Run `pytest server`.
