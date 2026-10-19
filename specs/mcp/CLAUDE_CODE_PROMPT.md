# Claude Code kickoff prompt

Paste this into Claude Code from the root of `surdej-v2/gaer-base`, with this folder at `specs/mcp/`.

---

Implement the two MCP servers described in `specs/mcp/`: `/mcp` for the app and `/mcp/admin` for admin.

Read first, in this order:
1. `CLAUDE.md` → `.gaer/agents/instructions.md`, then skills `gaer-backend`, `gaer-auth`, `gaer-audit`, `gaer-mcp`
2. `specs/mcp/README.md` and `specs/mcp/SPEC.md`
3. `specs/mcp/catalog.json` (source of truth for tools, modes, params and links)
4. Existing code: `server/app/sessions.py`, `server/app/routes/auth.py`, `server/app/routes/admin.py`,
   `server/app/logs.py`, `server/app/migrations/`, `web/src/admin/`
5. Reference (TypeScript, port the shapes): `happy-mates-apps/happy-groups` PR 14 —
   `apps/api/src/platform/mcp/registry.ts`, `transport.ts`, `superadmin/guard.ts`, `drafts.ts`, `links.ts`

Work in this order and commit after each step (`/gaer-db` for migrations):
1. Migration: `mcp_clients`, `mcp_tokens`, `mcp_drafts`, append-only `mcp_log` (+ grants, trigger),
   `<db>_mcp_ro` role in `deploy/provision_database.py`.
2. `server/app/mcp/oauth.py` + consent page `web/src/pages/McpConsent.tsx`. Login stays exactly as it is.
3. `transport.py` + `registry.py` (catalog check, audit wrapper) and mount both servers in `main.py`.
4. `drafts.py` + `confirm_draft` / `cancel_draft` / `list_drafts`.
5. `app_server.py` — all `server: "app"` tools.
6. `admin_server.py` — all `server: "admin"` tools, one domain at a time, `db_tools.py` last.
7. `app_map.json` generator (`npm run app-map --prefix web`), `links.py`, `/go` + `/admin/go` routes,
   `useDeepLinkFocus()`, `data-deeplink` anchors on the screens in the catalog.
8. `/api/admin/mcp/*` routes and the hub at `/admin/mcp` matching `ToerGaer Admin.dc.html`.
9. MCP clients under `/konto`; new env vars in `config.py`, `.env.example`, `deploy/.env.example`.
10. `server/tests/test_mcp.py` covering every item under "Verify" in SPEC.md.

Constraints:
- No new login path, no Node process, no ORM. Python `mcp` SDK only, pinned.
- App tools only ever touch the caller's own rows (`where user_id = %s`).
- Every admin change returns a draft; nothing changes before `confirm_draft`; `audit()` in the same transaction.
- Never expose: granting admin to yourself, log-table writes, raw SQL writes, session tokens.
- UI text Danish; tool names and descriptions English.
- `pytest server` and `npm run build --prefix web` pass before you say done.
