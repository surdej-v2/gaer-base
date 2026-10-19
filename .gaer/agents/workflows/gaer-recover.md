---
name: gaer-recover
description: Get back to a known good state after an error.
---

# /gaer-recover

Find out what is broken before you change anything. Ask before every step that changes production.

## Local
- Server will not start: read the traceback; `python -m app.migrate --status`; check `.env`.
- Database confused: `docker compose down -v && docker compose up -d && (cd server && python -m app.migrate)`
  — this deletes local data, ask first.
- Frontend: `rm -rf web/node_modules && npm ci --prefix web`.
- Code: `git stash` or `git restore` — never `git reset --hard` without asking.

## Production
1. Status: `gh run list --workflow=deploy.yml --limit 5`, `curl -fsS https://$DOMAIN/health`.
2. A failed rollout leaves the old version running — fix and push again.
3. A bad version that went live: on the instance, `./gaer/deploy.sh rollback` starts the previous image.
   Or `git revert` the commit and push to `main`.
4. A migration that broke data: managed PostgreSQL has automatic backups — restore to a point in time from
   the OVHcloud console into a new database, verify, then switch `DATABASE_URL`s. Involve Happy Mates.
   **Before the restored database goes live, re-apply GDPR erasures made after the backup time** — otherwise
   people who asked to be forgotten come back. Run with the instance's .env pointing at the restored database:
   ```bash
   python -m app.reapply_erasures --from-url "$OLD_DATABASE_URL" --since <backup time> --dry-run
   python -m app.reapply_erasures --from-url "$OLD_DATABASE_URL" --since <backup time>
   ```
   If the old database is gone, take the user ids from `gdpr.erasure` lines in any copy of the audit log and
   use `--ids 12,15`.
5. Logs on the instance: `docker logs --tail 200 gaer`, `docker logs gaer-caddy`.
6. Write down what happened and what was done.
