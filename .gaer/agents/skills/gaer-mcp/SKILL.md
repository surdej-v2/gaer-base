---
name: gaer-mcp
description: The MCP servers in Gær — /mcp for the app (acts as the user, own data), /mcp/admin for admin (admin role, every change is a draft) and the endpoints defined in MCP Studio on /mcp/<key>. Use when adding or changing an MCP tool, an endpoint, the OAuth flow, drafts or deep links.
---

# MCP servers (gaer-mcp)

Contract: `specs/mcp/SPEC.md`. Tools: `specs/mcp/catalog.json` — add a tool there first; a test fails if
the registered tools and the catalog differ. Code: `server/app/mcp/`.

| | `/mcp` | `/mcp/admin` | `/mcp/<key>` (MCP Studio) |
|---|---|---|---|
| Who | full session | `role = 'admin'`, not a test account, not impersonating; consent needs a fresh second factor | full session, and the endpoint's audience (all · internal · admin), checked on every request |
| Data | caller's own rows only | whole instance | caller's own rows only (the app's handlers) + Data tools from the catalog |
| Writes | own data directly; delete/export → draft | always draft → `confirm_draft` | as `/mcp` |

## Endpoints defined at runtime (`/developer/studio/mcp`)
- Every endpoint is a row in `mcp_endpoints` (migration 0030, `app/mcp/endpoints.py`): `app` and `admin` are `builtin`;
  the rest are made in MCP Studio with key (`^[a-z][a-z0-9-]{1,31}$`, not in `RESERVED`), name, description,
  instructions (sent on initialize), audience and enabled. `/mcp/{key}` is one Starlette route (`main.py`) served by
  `transport.DynamicEndpoints`: the row is read on every request, so a change takes effect at once; the session
  manager per key starts on first use inside the task group `mcp_sessions` holds open.
- Scope is `mcp` (`oauth.scope_for`), the resource URL is `/mcp/<key>`, metadata at
  `/.well-known/oauth-protected-resource/mcp/<key>`. A dynamic endpoint runs the **app's** Code tool handlers
  (`endpoints.handler_server`) — never admin tools — plus Data tools; its tool list is the endpoint map
  (`mcp_tool_endpoints.endpoint = key`). New endpoints start with the core tools (`studio.STARTER_TOOLS`).
- API: `/api/admin/studio` (`routes/studio.py`, admin section `/developer`, roles Data and Fuld): overview,
  `mcp-endpoints` CRUD (step-up), `PUT …/tools`, `…/tools/{name}/invoke` (as the admin, `Caller.via = "studio"`),
  `…/log`. Every change writes `audit()` (`mcp_endpoint.created/updated/deleted/tools`).
- `Caller.server` is the endpoint key; `mcp_log.server` and `mcp_drafts.server` hold it, so a draft made on
  `/mcp/salg` is confirmed there (`drafts.py` uses `caller.server`).

## Tools from Gær Developer (`specs/developer/RUNTIME.md`)
- A third kind of tool, `operation` (`mcp_tools.artifact_id`), is written when a Datastudie endpoint (artifact `mcp_endpoint`) is published; it runs `app/devkit/operations.py` (or a multisource source) through `mcp/operation_tools.py`, masked by the approved policy. Its level is its classification; external users do not see non-white operation tools. Arguments on non-white fields are masked in `mcp_log` (column `cache` holds hit/stale/miss). Endpoints with `artifact_id` are changed in Datastudie only. Rate limits per endpoint, user and client: `mcp_endpoints.limits`, checked in `registry.invoke`.

## Files
| File | What |
| --- | --- |
| `oauth.py` | well-known metadata, `/mcp/register`, `/mcp/authorize` → `/forbind` (consent), `/mcp/token`; a client is a `sessions` row (`mcp_client_id`, `mcp_scope`), so /konto, block and revoke end it |
| `transport.py` | Streamable HTTP with the `mcp` SDK (pinned); bearer → `Caller` on the ASGI scope; role re-checked per request |
| `registry.py` | loads `catalog.json` (copy of the spec's), `@tool` / `@draft`, `invoke()` — the one entry point, logs to `mcp_log` |
| `drafts.py` | `mcp_drafts`, `confirm_draft` / `cancel_draft` / `list_drafts`; confirm re-plans and compares `state_hash` |
| `app_server.py`, `admin_server.py`, `db_tools.py` | the tools; `db_query` runs as `<db>_mcp_ro` (`MCP_DB_QUERY_URL`) |
| `links.py`, `app_map.json` | deep links `/go?path=…&focus=…` / `/admin/go`, only if path + anchor are in the map |
| `hub.py` | `/api/admin/mcp/*` for the hub at `/admin/mcp` (overview, tools, invoke, drafts, events, SSE stream); confirming a draft there uses `require_admin_stepup` |

## Login and MFA (skill `gaer-auth`)
- The consent page `/forbind` uses the normal login: e-mail + code, and the second factor when the user's rules ask
  for one. A pending `mfa` session cannot consent.
- `mcp:admin` is only granted right after a second factor (`mfa_at` within `STEPUP_MINUTES`, else `403` +
  `X-Gaer-Error: stepup_required` — the app's step-up dialog handles it). The token then lives for weeks, so this is
  the moment MFA is checked; admin tools reuse the `/api/admin` helpers (`do_*`) with the same rules (super admin,
  last admin, who may reset whom).
- Profile and security tools: `/mcp` has `profile_get`, `profile_update` (name, language, time zone) and the
  read-only `security_get`; `/mcp/admin` has `user_get` (incl. MFA status), and the drafts `user_profile_set` and
  `user_reset_mfa`. Factors are never added or removed over MCP — that needs the browser (WebAuthn) and a second
  factor; link to `/konto#sikkerhed`.

## Files (skill `gaer-storage`, `server/app/filetree.py`)
- `/mcp`: `files_browse`, `file_read`, `file_write`, `folder_create`, `file_move` on the caller's home (`~/`) and
  `/shared/`; `file_delete` is a draft. `/mcp/admin`: `files_browse`, `file_read`, `file_link` on the whole tree;
  `file_write`, `folder_create`, `file_move`, `file_delete`, `folder_delete` are drafts. All go through `filetree`
  (same rules and audit rows as `/api/files` and `/admin/files`); links open `/admin/files?folder=…`.

## Which tool is where, and who may call it (specs/mcp/ACCESS.md)
- The **endpoint map** in the database decides which endpoint offers which tool (`mcp_tools`, `mcp_tool_endpoints`,
  `toolmap.py`); /admin/data → MCP-værktøjer edits it. A **Code tool** is a handler here; a **Data tool** reads a
  dataset from the catalog (`data_tools.py`). `invoke()` and `tools/list` resolve through the map.
- Classification decides who may call it: white everyone, green internal users, yellow internal + a grant the user
  approved, red internal + user and approver. Data tools follow their dataset; fields are masked by their own.
- `access_list` / `access_request` on both endpoints; approvals in /konto, /godkend (mail link) and /admin/access
  (or the `/mcp/admin` draft `access_request_decide`). Users are `internal` or `external` (`user_set_audience`).
- A new Code tool appears white on its catalog server(s) the first time it is seen.

## Adding a tool
- Add it to `specs/mcp/catalog.json` and copy the file to `server/app/mcp/catalog.json`.
- Read or write tool: `@tool("app", "name") async def run(args, caller) -> ToolOutput(data, links, text)`.
  App tools filter on `caller.user_id`; writes call `audit()` in the same transaction.
- Draft tool: `@draft("admin", "name") async def plan(args, caller, conn) -> DraftPlan(summary, impact, state, execute)`.
  `plan` must not change anything; `execute(conn)` runs in the confirm transaction next to the audit row.
- Raise `ToolError("…")` for a Danish message to the user. Names and descriptions are English.
- Give it a `link` in the catalog when a screen exists, add the `data-deeplink` anchor on that screen and run
  `npm run app-map --prefix web` (the build fails with `--check` when a catalog link has no anchor).

## Never
- A new login path — MCP authorizes through the normal login (code, second factor where required).
- Tools that add or remove passkeys, security keys or authenticator apps, or switch "Kræv passkey" on or off.
- App tools that read or write another user's rows.
- Tools that grant admin to yourself, write SQL, touch log tables or return a session token.
- A tool that approves its own access request, or any way for a client to skip the user's (or approver's) decision.
