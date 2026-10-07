---
name: gaer-audit
description: How the auth log and audit log are written, which events they must contain, and how log data is deleted. Use whenever code changes data, adds an admin action, or touches logs or retention.
---

# Logging (gaer-audit)

Code: `server/app/logs.py`, `server/app/retention.py`, migration `0001_init.sql` (grants, triggers).

## Two logs in Postgres, both append-only
- **auth_log** — `auth_event(conn, event, ok=..., meta=..., user_id=..., phone=..., **detail)`.
  Stores time, event, result, user id, masked phone + HMAC hash (searchable), IP, device, detail.
  Events: `otp.sent`, `otp.verified`, `otp.failed`, `grace.used`, `passkey.registered`, `passkey.login`,
  `passkey.deleted`, `passkey.reset`, `session.revoked`.
- **audit_log** — `audit(conn, action, actor_id=..., meta=..., table=..., row_id=..., before=..., after=..., **detail)`.
  Actions: `create`, `update`, `delete`, `file.upload`, `file.delete`, `role.changed`, `admin.first_user`,
  `admin.read`, `gdpr.request`, `gdpr.export`, `gdpr.erasure`, `log.exported`, `log.purged`, `session.revoked`.
  Auth events also include `phone.changed` (self-service with a code to the new number, or admin rectification).

## Rules
1. Write the log line **in the same transaction** as the change (same `conn`). No change without its log.
2. `before`/`after` hold only changed fields — use `changed(before, after)`. Never put secrets, codes, tokens or
   file contents in them; for long text log the length.
3. Everything in `/admin` is audited: changes explicitly, reads automatically (`admin.read` in `require_admin`).
4. The app's database user cannot UPDATE/DELETE/TRUNCATE log tables, and a trigger blocks UPDATE for everyone.
5. Deletion only through `retention.py` on the **purge** user (`PURGE_DATABASE_URL`), and every purge writes
   `log.purged` with counts in the same transaction. `log.purged` and `gdpr.erasure` entries are never deleted —
   they prove that data was erased and drive `app.reapply_erasures` after a backup restore.
6. Automatic retention runs daily (`maintenance_loop`); periods are set in `/admin/retention`.

## Checklist for new code
- [ ] each create/update/delete calls `audit()`
- [ ] failures that matter for security call `auth_event(ok=False)`
- [ ] a test asserts the expected log entries
