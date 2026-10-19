# Claude Code kickoff prompt

Paste from the root of `surdej-v2/gaer-base`, with this folder at `specs/mobile/`.

---

Build the Gær mobile app described in `specs/mobile/`, plus the /admin section App Stores.

Read first: `CLAUDE.md` → `.gaer/agents/instructions.md`, skills `gaer-backend`, `gaer-auth`, `gaer-vite`;
`specs/mobile/README.md`, `specs/mobile/SPEC.md`; existing `web/src/mobile/Mobile.tsx`, `web/src/bridge/`,
`server/app/routes/auth.py`. Reference: happy-groups `apps/native/` and `modules/member-brand-portal/`.

Order (commit after each):
1. Backend: `/api/auth/handoff` + redeem, `devices` table + routes, well-known files, `/api/admin/app-store`.
2. `mobile/` Expo app: config from env, screens 1–4, secure session storage.
3. WebView shell + bridge (`gaer-native`), deep links, push.
4. /admin → App Stores (`web/src/admin/AppStores.tsx`) matching `ToerGaer Admin.dc.html`.
5. `.github/workflows/mobile.yml` and `eas.json`.
6. Tests listed under Verify.

Constraints: no white-label (no brands, no APP_BRAND); login rules unchanged; no secrets in the repo;
UI text Danish; `pytest server` and `npm run build --prefix web` pass.
