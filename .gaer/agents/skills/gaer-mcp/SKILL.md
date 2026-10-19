---
name: gaer-mcp
description: The two MCP servers in Gær — /mcp for the app (acts as the user, own data) and /mcp/admin for admin (admin role, every change is a draft). Use when adding or changing an MCP tool, the OAuth flow, drafts or deep links.
---

# MCP servers (gaer-mcp)

Contract: `specs/mcp/SPEC.md`. Tools: `specs/mcp/catalog.json` — add a tool there first; a test fails if
the registered tools and the catalog differ. Code: `server/app/mcp/`.

| | `/mcp` | `/mcp/admin` |
|---|---|---|
| Who | full session | `role = 'admin'`, not a test account, not impersonating |
| Data | caller's own rows only | whole instance |
| Writes | own data directly; delete/export → draft | always draft → `confirm_draft` |

## Adding a tool
- Read tool: `@tool("name", server="app"|"admin")` with `run(args, ctx) -> ToolOutput(text, data, links)`.
  Set `read_only=True` so clients can auto-approve it.
- Draft tool: `plan(args, ctx) -> DraftPlan(summary, impact, state, execute)`. `plan` must not change anything.
  `execute` runs inside `db.connection()` and writes `audit()` in the same transaction.
- Summaries and errors are Danish (they show in /admin); names and descriptions English.
- Give it a `link` in the catalog when a screen exists, add the `data-deeplink` anchor, run
  `npm run app-map --prefix web`.

## Never
- A new login path — MCP authorizes through the normal phone + code + passkey login.
- App tools that read or write another user's rows.
- Tools that grant admin to yourself, write SQL, touch log tables or return a session token.
