---
name: gaer-new-feature
description: Plan and build a new feature with tests and audit logging from the start.
---

# /gaer-new-feature

1. Run `/gaer-boot` if you have not read the project in this session.
2. Clarify with the user: who uses it (users, admins, anonymous), what data it stores, and where it shows
   up (app, `/m`, `/extension`, Office, `/w`, `/admin`).
3. Design before code:
   - **Data**: a migration (see `/gaer-db`). Owned by a user → `user_id ... on delete cascade`.
   - **API**: routes in `server/app/routes/<feature>.py` following `gaer-python-api`.
     Every change writes `audit()` in the same transaction (see `gaer-audit`).
   - **UI**: pages following `gaer-vite`. Admin pages go under `/admin/<feature>` and in `admin/sections.ts`.
   - **GDPR**: add the data to `build_user_export` in `server/app/gdpr.py` and make sure `erase_user` removes it.
4. Write tests first or alongside — `server/tests/test_<feature>.py`. At minimum: the happy path, another
   user cannot see it, and the audit log has the expected entries.
5. Build, then run:
   ```bash
   pytest server
   npm run build --prefix web
   ```
6. Show the user what changed and suggest `/gaer-pr`.
