---
name: gaer-db
description: Create and run migrations in Postgres, including log tables and grants.
---

# /gaer-db

Migrations are plain SQL in `server/app/migrations/NNNN_name.sql`, applied in order by
`python -m app.migrate` (one transaction per file, advisory lock, `schema_migrations` table).

## New migration
1. Pick the next number: `ls server/app/migrations`.
2. Write `NNNN_short_name.sql`. Rules:
   - Never edit a migration that has been applied anywhere. Write a new one.
   - New tables get grants automatically: `gaer_apply_grants()` runs after every migration and gives the
     `<db>_app` user SELECT/INSERT/UPDATE/DELETE on all tables **except** the log tables.
   - A new **log-like** table (append-only) must be added to `log_tables` in a new version of
     `gaer_apply_grants()` and get a `before update` trigger with `gaer_reject_log_update()`.
   - Personal data columns: make sure GDPR export (`build_user_export` in `server/app/gdpr.py`) and
     erasure (`erase_user` there, `retention.purge_user` for logs) cover the new table. A FK to `users` with
     `on delete cascade` handles erasure; the export needs an explicit entry.
   - Foreign keys to `users`: decide `on delete cascade` (owned data) vs `set null` (shared records).
3. Apply and check:
   ```bash
   (cd server && python -m app.migrate && python -m app.migrate --status)
   pytest server
   ```
4. Add a test if the migration has rules (grants, triggers, constraints) — see `server/tests/test_logs.py`.

## Roles in production
`<db>_owner` runs migrations (`MIGRATION_DATABASE_URL`), `<db>_app` runs the app (`DATABASE_URL`),
`<db>_purge` deletes log data from /admin (`PURGE_DATABASE_URL`). Created by `deploy/provision_database.py`.
