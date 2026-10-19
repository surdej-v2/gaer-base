# Gær MCP — `/mcp` (appen) og `/mcp/admin` (admin)

Super prompt for Claude Code. Target repo: `surdej-v2/gaer-base`. Reference: `happy-mates-apps/happy-groups` PR 14
(`apps/api/src/platform/mcp/` — `registry.ts`, `transport.ts`, `superadmin/{guard,drafts,links,server}.ts`).
Gær is Python, so the shapes are ported, not the code.

## Goal

Two MCP servers in the same container as the app, next to `/api`:

| | `/mcp` — appen | `/mcp/admin` — admin |
|---|---|---|
| Who | Any user with a full session | Only `users.role = 'admin'`; not a test account; not while impersonating |
| Acts as | The signed-in user, own data only | The admin, across the whole instance |
| Scope | `mcp` | `mcp:admin` |
| Writes | Own data directly (create/update a clip, own ring level). Delete and GDPR export → draft | Every change → draft → `confirm_draft(draftId, reason)` |
| Tools | 15 (`catalog.json`, `server: "app"`) | 34 (`catalog.json`, `server: "admin"`) |
| Off switch | `MCP_APP_ENABLED=0` → 404 | `MCP_ADMIN_ENABLED=0` → 404 |

`catalog.json` is the source of truth for tool names, modes, parameters and deep links. A test fails if the
registered tools and the catalog differ.

## Stack

- Python package `mcp` (official SDK), `FastMCP` with **Streamable HTTP**, `stateless_http=False`, mounted in
  `server/app/main.py`:
  ```python
  from app.mcp import app_server, admin_server
  app.mount("/mcp/admin", admin_server.asgi())   # mount the longer path first
  app.mount("/mcp", app_server.asgi())
  ```
  Pin it in `server/requirements.txt`. No Node, no second process.
- New package `server/app/mcp/`:
  ```
  __init__.py
  oauth.py        /.well-known/*, /mcp/register, /mcp/authorize, /mcp/token, token validation
  transport.py    bearer → Principal, per-request caller context (contextvars), session map
  registry.py     @tool decorator: catalog check, audit wrapper, readOnly/destructive hints
  drafts.py       create / confirm / cancel / list (table mcp_drafts)
  links.py        deep links, validated against app_map
  app_map.py      /admin sections + app routes + data-deeplink anchors (generated JSON, bundled)
  db_tools.py     db_query / db_schema on the read-only role
  app_server.py   tools for /mcp
  admin_server.py tools for /mcp/admin
  ```
- The existing CSRF middleware exempts `/mcp` paths (they use a bearer, never a cookie).

## Auth — reuses Gær login

1. Unauthenticated request → 401 with
   `WWW-Authenticate: Bearer resource_metadata="<ORIGIN>/.well-known/oauth-protected-resource/mcp[/admin]"`.
2. `/.well-known/oauth-authorization-server` → authorize, token and registration endpoints,
   `scopes_supported: ["mcp", "mcp:admin"]`, `code_challenge_methods_supported: ["S256"]`.
3. `POST /mcp/register` — dynamic client registration (public clients, PKCE required).
4. `GET /mcp/authorize` — if not signed in, redirect to `/login?next=…` (phone + code, passkey rules
   unchanged — **no new login path**). Then a consent page in `web/src/pages/McpConsent.tsx` (Danish):
   client name, scope, what it may do. `mcp:admin` is refused at consent unless the user is admin.
5. `POST /mcp/token` — code → access token (1 h) + refresh token (30 days). Tokens are random, only the
   SHA-256 is stored (table `mcp_tokens`, same pattern as `sessions`). Refresh rotates.
6. Every request: token → `Principal` (reuse `sessions.Principal`, `scope='full'`) → for `/mcp/admin`,
   `require_mcp_admin()` re-checks `role == 'admin'`, `not impersonation_only`, `impersonator_id is null`.
   A blocked user or a revoked session ends the token immediately (`user_revoke_sessions` covers MCP too).
7. Users see and revoke their MCP clients under `/konto` (Sessioner). `me_sessions` lists them.

## Drafts (both servers)

Ported from happy-groups `drafts.py` semantics:

```sql
create table mcp_drafts (
  id          text primary key,              -- d_<8 hex>
  server      text not null check (server in ('app','admin')),
  tool_name   text not null,
  args        jsonb not null,
  summary     text not null,                 -- Danish, shown in /admin and to the model
  impact      jsonb not null,                -- { bullets, counts, diff, warnings }
  links       jsonb not null default '[]',
  state_hash  text not null,                 -- sha256 of stable JSON of what plan() read
  status      text not null default 'pending', -- pending|confirmed|cancelled|expired|failed
  created_by  int  not null references users(id) on delete cascade,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,          -- + MCP_DRAFT_TTL_MIN (10)
  confirmed_at timestamptz, reason text, result jsonb
);
```

- A mutating tool implements `plan(args, ctx) -> DraftPlan(summary, impact, state, execute)`. `plan` changes nothing.
- `confirm_draft`: only the creator; claim with `update … where status='pending' and expires_at > now()`
  (exactly once); re-plan; `state_hash` must match or nothing runs; then `execute()` inside
  `db.connection()` with `audit()` in the same transaction (`action='mcp.<tool>'`, `via='mcp:<server>'`, reason).
- Drafts are personal data → included in `build_user_export`, cascade on erasure.

## Audit

- Every MCP call writes one row to a new **append-only** log table `mcp_log` (add it to `log_tables` in a new
  `gaer_apply_grants()` and give it the `gaer_reject_log_update()` trigger): time, server, user, client,
  method, tool, truncated args (strings > 500 chars cut), duration, status
  (`ok|error|auth_failed|forbidden|draft|confirmed|cancelled`), masked IP.
- Changes also write the normal `audit()` row. Retention of `mcp_log` is configurable in `/admin/retention`.

## Deep links

`<ORIGIN>/admin/go?path=/admin/users&userId=4&focus=user-block` and the same for app routes
(`/go?path=/klip/12&focus=clip-body`).

- `links.py` builds them from the tool's `link` in `catalog.json` and validates `path` and `focus` against
  `app_map.json`, so a dead link is never returned. List tools return one link for the list plus one per row
  (max 20). Draft tools include the link in the draft and again after confirm.
- Frontend: route `/go` and `/admin/go` → navigate to `path` with the args; hook `useDeepLinkFocus()` waits
  for `[data-deeplink="<focus>"]` (MutationObserver, 5 s), opens a hidden parent
  (`data-deeplink-parent`), scrolls with `window.scrollTo`, focuses it and rings it for 2 s.
- `npm run app-map --prefix web` scans `web/src` for `data-deeplink` per route and writes
  `server/app/mcp/app_map.json`. CI fails if a catalog `link.focus` has no anchor.

## Database tools (admin only)

`db_query` accepts one `SELECT` / `WITH` / `EXPLAIN` (parsed, not regexed), runs in
`BEGIN READ ONLY; SET LOCAL statement_timeout = '5s'` on a new `<db>_mcp_ro` role (SELECT only, no log
tables with phone hashes in clear), max 500 rows, masks `phone`, `code_hash`, `token_hash`, `public_key`,
`secret*` and phone-shaped values. `deploy/provision_database.py` creates the role;
`MCP_DB_QUERY_URL` points at it. Without it the tool answers "ikke tilgængelig".

## /admin hub

Section `/admin/mcp` in the standalone admin app (design: `ToerGaer Admin.dc.html` → *MCP-servere*):
server switch (Appen / Admin) and tabs Oversigt · Værktøjer · Kladder · Monitor · Forbind.

- `GET /api/admin/mcp/overview?server=` — endpoint, scope, clients, 30-day calls/errors/callers.
- `GET /api/admin/mcp/tools?server=` — catalog with mode, domain and link template.
- `POST /api/admin/mcp/tools/{name}/invoke?server=` — runs a tool as the admin (app tools run as the admin's
  own user). Draft tools return the draft.
- `GET /api/admin/mcp/drafts`, `POST /api/admin/mcp/drafts/{id}/confirm {reason}`, `…/cancel`.
- `GET /api/admin/mcp/stream?server=` — SSE from `mcp_log` (LISTEN/NOTIFY on insert).

## Client config

```bash
claude mcp add --transport http gaer       https://<domæne>/mcp
claude mcp add --transport http gaer-admin https://<domæne>/mcp/admin
```

`.vscode/mcp.json`:
```json
{ "servers": {
  "gaer":       { "type": "http", "url": "https://<domæne>/mcp" },
  "gaer-admin": { "type": "http", "url": "https://<domæne>/mcp/admin" } } }
```

## Never

- A tool that grants the admin role to yourself, edits log tables, writes SQL, or hands out a session token.
- A way to sign in that skips the code or passkey rules (MCP uses the normal login).
- App tools that read or change another user's data.

## Verify (`server/tests/test_mcp.py`)

- 401 without token; `/mcp/admin` 403 for a non-admin, a test account and an impersonated session.
- `tools/list` on each server equals `catalog.json` for that server.
- App: `clips_list` only returns own clips; `clip_get` on another user's clip → not found.
- Draft tool returns a draft; `confirm_draft` runs once, second confirm fails, expired fails,
  changed state fails and changes nothing.
- `db_query` rejects `UPDATE`, `DROP`, `;`-chained statements and runs as `<db>_mcp_ro`.
- Every call writes `mcp_log`; the app user cannot UPDATE/DELETE it.
- Revoking sessions or blocking a user invalidates their MCP tokens.
- Every catalog `link` resolves in `app_map.json`.
- `pytest server` and `npm run build --prefix web` pass.
