# Claude Code kickoff prompt

Kør fra roden af `surdej-v2/gaer-base`, med mappen `specs/` fra denne pakke kopieret ind (og `.gaer/`, `.claude/`,
`.github/` fra pakken lagt over de eksisterende).

---

Byg den selvstændige admin-app beskrevet i `specs/admin-app/`.

Læs først, i denne rækkefølge:
1. `CLAUDE.md` → `.gaer/agents/instructions.md`, skills `gaer-backend`, `gaer-auth`, `gaer-audit`, `gaer-vite`, `gaer-mcp`
2. `specs/admin-app/README.md` — især **Placering i repoet** og skærmtabellen
3. `specs/admin-app/screenshots/*.png` og prototypen `specs/admin-app/design/ToerGaer Admin.dc.html` (åbn i en browser)
4. Eksisterende kode: `web/src/App.tsx`, `web/src/admin/*`, `web/src/ui.tsx`, `web/src/api.ts`, `web/src/auth.tsx`,
   `web/src/bridge/extension.ts`, `server/app/main.py`, `server/app/routes/admin.py`, `server/app/sessions.py`
5. Referencer: `happy-mates-apps/happy-groups` (`apps/frontend/src/routes/admin/ImpersonationPage.tsx`,
   `modules/member-features/ui/src/hq/FeaturesAdmin.tsx`, `apps/frontend/src/core/tracer/*`,
   `modules/member-brand-portal/*`), `happy-management/surdej-v1` (`apps/extension/src/shared/protocol.ts`,
   `apps/frontend/src/core/extension/useBridge.ts`)

Rækkefølge (commit efter hvert trin, `/gaer-db` for migrationer):
1. Vite multi-page: `web/admin.html` + `web/src/admin-app/main.tsx`; `main.py` serverer `admin.html` for `/admin/*`.
   Appen på `/` må ikke ændre adfærd.
2. `AdminShell` (menu, topbar, banner) og flyt de eksisterende /admin-sider ind uændret.
3. Statistik og Brugere (genbrug `/api/admin/analytics`, `/api/admin/users`), bruger-skuffen.
4. Efterligning: migration `0003`, `routes/impersonation.py`, banner, tests.
5. Features og ringe: migration `0004`, `routes/features.py`, `FeatureGate` i appen, tests.
6. API-tracer (overlay + panel + interceptor i `api.ts`).
7. Happy AI-knap, popover, PING-handshake og installationsguide; udvid `bridge/extension.ts`.
8. App Stores: migration `0005`, `routes/app_store.py`, fanerne. Link til sidepanel-guiden.
9. MCP-hubben, når `specs/mcp/` er bygget (eget forløb: `specs/mcp/CLAUDE_CODE_PROMPT.md`).
10. `PUT /api/admin/app-store` skal tage imod det, sidepanel-guiderne sender: Apple (Team ID, Issuer ID, Key ID),
    Google (Developer ID, servicekonto-e-mail) og Expo (organisation, EAS Project ID). Vis dem under Links og ID'er
    og brug dem i tjeklisten. Selve sidepanelet bygges i `surdej-v1`, ikke her (skærm 13–19).

Regler:
- Login-reglerne ændres ikke. Testkonti kan ikke logge ind selv.
- Alt i `/api/admin` kræver `require_admin` og skriver `audit()` i samme transaktion.
- Nye persondata i GDPR-eksport og sletning.
- UI-tekst dansk og ordret fra prototypen. Ingen white-label i App Stores.
- `pytest server` og `npm run build --prefix web` skal bestå, før du siger, du er færdig.
