# Mail, kalender og kontakter — Stalwart + Lettermint på UpCloud

The spec is the doc *Mail module spec — Stalwart + Lettermint on UpCloud* (Claude Docs). This file records how it is
built in Gær, where the build differs from the spec, and what still has to be checked against the Stalwart version
you deploy.

## What is built

| Part | Where |
| --- | --- |
| Tables: domains, mailboxes (personal, shared), aliases + alias requests, shared members, identities, calendars and address books + shares, working hours, app passwords, retention policies + runs, delivery events | `server/app/migrations/0017_mail.sql` |
| Stalwart client (management API, JMAP and WebDAV via the master user) and an in-memory stand-in | `server/app/mailbox/backend.py` |
| Rules: provisioning, roles, identities, collections, app passwords, retention | `server/app/mailbox/service.py` |
| DNS records per domain and the live check | `server/app/mailbox/dns.py` |
| Jobs `mail.provision`, `mail.retention`, `mail.sync`, `mail.backup`, `mail.archive_export`, `mail.principal.delete`, and the incoming-webhook handler `lettermint` | `server/app/mailbox/tasks.py` |
| `/api/mail` (the user, shared-mailbox managers) | `server/app/routes/mail.py` |
| `/api/admin/mail` (operators, section `/mail`, role Drift) | `server/app/routes/admin_mail.py` |
| `/user/mail`, `/user/<id>/mail` | `web/src/pages/Mail.tsx` |
| `/admin/mail` (Overblik, Domæner, Postkasser, Fælles postkasser, Kalendere og kontakter, Afsendelse, Opbevaring, Drift, Audit), `/admin/mail/users/<id>` | `web/src/admin-app/pages/mail/` |
| Tests | `server/tests/test_mail.py` |
| Phase 1: UpCloud (Terraform), Stalwart config, deploy and bootstrap scripts, runbook | `deploy/mail/` |

Phase 1 (Stalwart on UpCloud with Managed PostgreSQL and Object Storage, the Lettermint relay, TLS) is in
`deploy/mail/`: Terraform for UpCloud, Stalwart's `config.toml` and compose file, and the runbook in
`deploy/mail/README.md`.

## Where Gær differs from the spec

- **Paths.** Gær's own pages live under `/user`, so the spec's `/users/mail` is `/user/mail` and `/users/<id>/mail` is
  `/user/<id>/mail` (your own id only; anyone else gets "not found", like the API's 404). The admin view of one user is
  `/admin/mail/users/<id>` in the admin app, because everything an admin does goes through `/api/admin` (admin roles,
  `admin.read` logging, step-up). So the spec's `/api/mail/admin/*` is `/api/admin/mail/*`, and admin actions on a
  user (`POST /api/mail/users/<id>/mailbox` …) are `/api/admin/mail/users/<id>/…`. `me` works wherever `<id>` does
  under `/api/mail`.
- **Lettermint webhooks** use Gær's incoming webhooks instead of `/api/mail/webhooks/lettermint`: create an endpoint
  in `/admin/webhooks → Indgående` with handler `lettermint` and HMAC verification, and give its URL to Lettermint.
  It gets verification, IP filter, rate limit, dedupe and retries for free.
- **Destructive admin actions** (suspend, delete/convert, revoke all app passwords, rotate relay credentials, delete
  shared mailbox/collection) need a fresh second factor as well as a reason.
- **Force sign-out of clients** is "revoke all app passwords": clients only ever log in with app passwords.
- **Archive export** writes .eml files under `MAIL_ARCHIVE_PREFIX` in the app's Object Storage (point it at a
  separate bucket with its own lifecycle rule in production), and the user's download lands as a zip in their own
  files under `/Mail-arkiv/`.
- **Inbound volume per day** is not shown: it lives in Stalwart's metrics. Outbound comes from Lettermint events.
- **Suppressed recipients** are managed in Lettermint; the Sending tab shows recent bounces and complaints.
- **App password "last used"** is not reported by Stalwart through the management API and shows "—".

## Running it

```
MAILBOX_BACKEND=stalwart
STALWART_URL=http://10.0.0.5:8080          # private network only
STALWART_API_KEY=…                          # Bearer, admin permissions
STALWART_MASTER_USER=gaer-master            # master user: the backend acts as "<account>%gaer-master"
STALWART_MASTER_SECRET=…
MAIL_HOSTNAME=mail.firma.dk                 # MX target and client host
```

`MAILBOX_BACKEND=memory` runs every page without Stalwart (development; refused in production).

## Check against the deployed Stalwart version

The client keeps every call in `backend.py`. Verify before go-live:

1. Principal API: `GET/POST/PATCH/DELETE /api/principal`, fields `emails`, `quota`, `usedQuota`, `memberOf`,
   `disabledPermissions` (`authenticate`, `email-receive` for suspension), and app passwords as secrets named
   `$app$<name>$<secret>`.
2. Master user login (`<account>%<master>`) over HTTP Basic for JMAP and WebDAV.
3. WebDAV paths `/dav/cal/<account>/<slug>/`, `/dav/card/<account>/<slug>/`, principals `/dav/pal/<name>/`, the
   `ACL` method, `calendar-availability` and `schedule-calendar-transp`.
4. Shared mailbox readers via JMAP `Mailbox/set shareWith`; senders and managers as group members.
5. Queue: `GET /api/queue/messages`, `PATCH /api/queue/status/{stop,start}`, `PATCH /api/queue/messages`; relay
   credentials via `POST /api/settings` (prefix `queue.route.lettermint.auth`) + `GET /api/reload`.
6. Whether Stalwart's own auto-expunge can cover all folders; if so the retention job shrinks to configuration.
