# Handoff: Standalone /admin-app til Gær (gaer-base)

## Overview
En selvstændig admin-app under `/admin`, der kører side om side med appen på `/` i samme container.
Den samler statistik, brugeradmin med efterligning, features og ringe, App Stores-opsætning for
mobil-appen og en hub til de to MCP-servere. Dertil kommer tre ting, der ligger oven på alle sider:
Happy AI-udvidelsen i topbaren, guiden til at installere udvidelsen og den flydende API-tracer.
Et separat Happy AI-sidepanel guider til oprettelse af udviklerkonti hos Apple og Google.

Kilder: brugeradmin, efterligning, features og ringe og API-tracer er taget fra
`happy-mates-apps/happy-groups`, udvidelsens bro fra `happy-management/surdej-v1`, MCP-modellen fra
happy-groups PR 14 og App Stores fra happy-groups `modules/member-brand-portal`, uden white-label.

## About the design files
Filerne i `design/` er **designreferencer lavet i HTML**: prototyper med mock-data, der viser layout og
adfærd. Det er ikke produktionskode. Opgaven er at **genskabe dem i `web/`** (Vite + React + TypeScript)
med gaer-base's egne mønstre (`web/src/ui.tsx`, `web/src/api.ts`, `useAuth()`) og et backend i
`server/` (FastAPI, psycopg, rå SQL). Åbn `design/ToerGaer Admin.dc.html` i en browser for at klikke rundt.
Tweaks-panelet kan starte på en bestemt sektion og skifte udvidelsens tilstand (forbundet, forældet,
ikke installeret).

## Fidelity
**High-fidelity.** Farver, typografi, afstande og tekster er endelige. Genskab dem pixel-præcist.
Alt UI-tekst er dansk og skal bruges ordret.

Bemærk: i skærmbillederne vises lucide-ikonerne som grå firkanter. Det skyldes screenshot-værktøjet
(CSS `mask`). Ikonerne er lucide `0.562.0` og vises korrekt i prototypen.

## Placering i repoet

Admin-appen er en **selvstændig app**: egen Vite-entry, egen layout-shell, egen bundle. Den deler
login-cookie, `api.ts` og `auth.tsx` med appen på `/`, men ikke `TopBar`. Den eksisterende
`web/src/admin/` erstattes af den nye app. De nuværende sektioner (auth-log, audit-log, GDPR, sletning,
samtykke, filer, website, integrationer, indstillinger) flyttes ind uændret som undersider.

```
web/
  index.html                  appen på /
  admin.html                  NY · entry for /admin (Vite multi-page: build.rollupOptions.input)
  src/
    admin-app/                NY · den selvstændige admin-app
      main.tsx                mount + BrowserRouter basename="/admin"
      AdminShell.tsx          mørk sidemenu, sticky topbar, efterligningsbanner      → 01
      nav.ts                  menugrupper: Overblik · Adgang · Produkt · Integrationer · System
      pages/
        Stats.tsx             /admin                                                  → 01
        Users.tsx             /admin/users  + UserDrawer.tsx                          → 02, 03
        Impersonation.tsx     /admin/impersonation                                    → 04
        Features.tsx          /admin/features                                         → 05
        AppStores/            /admin/app-stores  (Overview, Assets, Texts, Links,     → 06–08
                              Privacy, Guide)
        Mcp/                  /admin/mcp  (Overview, Tools, Drafts, Monitor, Connect) → 09, 10
        (eksisterende)        logs/auth, logs/audit, gdpr, retention, consent, files,
                              web, integrations, settings — flyttet fra src/admin/
      extension/
        ExtensionButton.tsx   Happy AI-ikon i topbaren, venstre for „Åbn appen“       → 11
        ExtensionPopover.tsx  status, aktuel fane, markering, handlinger, instans, bro-log
        InstallGuide.tsx      sidepanel når handshake mangler (Chrome Web Store)
      tracer/
        TracerOverlay.tsx     flydende, trækbart mærke nederst til venstre            → 12
        TracerPanel.tsx       panel med kald, YAML-eksport, sidekontekst
        tracerStore.ts        fetch-interceptor på api.ts
    bridge/extension.ts       findes · udvides med GET_VERSION, GET_PAGE_INFO, GET_SELECTION,
                              OPEN_SIDE_PANEL, GET_INSTANCES, SET_INSTANCE, OPEN_OPTIONS
server/app/
  main.py                     SPA-fallback: /admin/* → web/dist/admin.html, resten → index.html
  routes/admin.py             findes · statistik, brugere
  routes/impersonation.py     NY · /api/impersonation/*
  routes/features.py          NY · /api/features/*, /api/admin/features/*
  routes/app_store.py         NY · /api/admin/app-store
  mcp/                        NY · se specs/mcp/SPEC.md
  migrations/0005_*.sql …     NY · én fil pr. område (se nedenfor)
mobile/                       NY · se specs/mobile/SPEC.md
```

Happy AI-sidepanelet (13–19) bygges **ikke** i gaer-base. Det hører til Happy AI-udvidelsen
(`happy-management/surdej-v1` → `apps/frontend/src/routes/extension/`) som værktøjet
`/extension/tools/app-stores`, ved siden af `toergaer`. Admin-appen linker dertil fra App Stores.

## Screens

| # | Skærm | Fil | Route |
|---|---|---|---|
| 01 | Statistik | `screenshots/01-statistik.png` | `/admin` |
| 02 | Brugere og roller | `02-brugere.png` | `/admin/users` |
| 03 | Bruger (skuffe) | `03-bruger-detalje.png` | `/admin/users?user=<id>` |
| 04 | Efterligning | `04-efterligning.png` | `/admin/impersonation` |
| 05 | Features og ringe | `05-features-og-ringe.png` | `/admin/features` |
| 06 | App Stores · Overblik | `06-app-stores-overblik.png` | `/admin/app-stores` |
| 07 | App Stores · Grafik | `07-app-stores-grafik.png` | `/admin/app-stores/grafik` |
| 08 | App Stores · Tekster | `08-app-stores-tekster.png` | `/admin/app-stores/tekster` |
| 09 | MCP · Oversigt | `09-mcp-oversigt.png` | `/admin/mcp` |
| 10 | MCP · Værktøjer | `10-mcp-vaerktoejer-kladde.png` | `/admin/mcp/tools` |
| 11 | Udvidelse · popover | `11-udvidelse-popover.png` | overlay |
| 12 | API-tracer | `12-api-tracer.png` | overlay |
| 13–16 | Happy AI-sidepanel · udviklerkonti (portal, Apple, Google) | `13–16-sidepanel-*.png` | `/extension/tools/app-stores` |
| 17–19 | Happy AI-sidepanel · Expo · EAS (trin 1, trin 4, tjek) | `17–19-sidepanel-expo-*.png` | `/extension/tools/app-stores/expo` |

### Shell (alle sider)
- **Sidemenu**: 232 px, `#0f172a`, sticky, fuld højde, padding `20px 10px 16px`. Øverst Tørgær-mærket
  (`ToergaerMark`, variant a, 28 px, uden byline, `dark`) og `/admin · <instans>` i mono 11 px, 60 % hvid.
  Grupper: label 10 px/700, tracking 0.1em, versaler, 45 % hvid. Punkt: 13 px, padding `8px 10px`,
  radius 8 px. Aktiv: bg `rgba(255,255,255,0.1)`, 600, hvid, 2×16 px streg til venstre i `#fcbf49`. Inaktiv: 72 % hvid.
  Badges 18 px høje: rød `#dc2626` (tal), amber `#f59e0b` med `#0f172a` tekst (efterligning aktiv, kladder,
  manglende features). Bund: hairline 10 % hvid, grøn pulserende prik + „Server online · v<version>“, link „← Til appen på /“.
- **Topbar**: sticky, `rgba(255,255,255,0.92)` + `backdrop-filter: blur(8px)`, bund `1px #e2e8f0`, padding `10px 24px`.
  Venstre: `<domæne>` + **sti** i mono 13 px (stien 600 `#020817`). Periodeskifter 7/30/90 dage kun på Statistik.
  Health-pille. Højre: **Happy AI-knap** (32 px rund, logo 22 px, statusprik 10 px nederst til højre:
  grøn `#10b981` forbundet, amber `#f59e0b` forældet, grå `#94a3b8` mangler), „Åbn appen ↗“ (outline, 32 px), avatar 30 px med brand-gradient.
- **Efterligningsbanner** over topbaren, når efterligning er aktiv: `#fffbeb`, bund `rgba(245,158,11,0.4)`,
  tekst `#78350f`, knapper „Åbn / som <navn>“ (outline) og „Stop efterligning“ (`#78350f`).
- **Indhold**: padding `28px 24px 64px`, `max-width: 1240px`, gap 20 px. Sidetitel 26 px/700, tracking -0.02em;
  undertekst `#64748b`, max 720 px. Kort: hvid, `1px #e2e8f0`, radius 16 px, padding 18–20 px.

### 01 Statistik
KPI-gitter `auto-fit minmax(200px,1fr)`: Brugere, Aktive brugere, Sidevisninger, Logins. Label 11 px/700 versaler
`#64748b`, tal 30 px/700 tabular. Søjlediagram 180 px: pr. dag en blå søjle (sidevisninger, `#2563eb` 85 %) og en
amber (logins, `#f59e0b`). Tre kort: Mest brugte features (orange gradient-barer + ringchip), Sider (blå barer),
Login (stablet bar grøn/blå/rød + „Brugere med passkey“, „Skal oprette passkey“ og link til filteret).

### 02–03 Brugere og roller
Søgning + filterchips (Alle, Aktive, Spærrede, Uden passkey, Admins, Testkonti) med antal. Rækker i grid
`minmax(240px,1.6fr) 110px 170px 130px 100px 40px`. Piller 11 px/700: Admin `#0f172a`/hvid, Bruger `#f1f5f9`/`#475569`,
passkey grøn, engangskoder blå, „Skal oprette passkey“ orange, Spærret rød, Testkonto amber.
Klik på en række åbner en **skuffe** på 420 px fra højre med fakta og handlinger: Gør til admin / Fjern admin (deaktiveret
for sidste admin), Spær / Ophæv (ikke dig selv), Nulstil passkeys, Log ud alle steder, Se auth-log. Testkonti får en
amber boks med „Skift til“.

### 04 Efterligning
Fra happy-groups `ImpersonationPage.tsx`, uden grupper: „Opret testkonto“ (navn, rolle Bruger/Admin), „Én pr. rolle“,
liste „Dine testkonti“ med „Skift til“ / „Aktiv“ og slet. En testkonto kan ikke logge ind selv og ejes af den admin,
der oprettede den. `/admin` bruger altid den rigtige admins rolle; kun appen på `/` kører som testkontoen.

### 05 Features og ringe
Fra happy-groups `FeaturesAdmin.tsx`: ringforklaring (4 kort), tabel med feature/nøgle, område, beskrivelse, ring
(chip + select), observation (toggle 36×20), „Brug 30 d“ (klik folder brugerne ud), slet for egne features.
„Importér fra koden (n)“ og „Ny feature“ (dialog: nøgle `^[a-z0-9][a-z0-9-]*$`, titel, beskrivelse, område; starter i ring 1).
„Brugernes niveau“ med vælg bruger + ring + Sæt / Nulstil. Ringfarver: 1 rød, 2 orange, 3 blå, 4 grøn.

### 06–08 App Stores (uden white-label)
Fra happy-groups `StoreStudio.tsx`, men **ingen brand-skifter, intet „Nyt brand“, ingen ikonforslag, ingen brand-fil**.
Øverst et link-kort til Happy AI-sidepanelet. Butiksskifter App Store / Google Play, link til konsollen, DA/EN.
Faner: Overblik (app-kort + „klar til indsendelse“-bar + tjekliste med „Gå til →“ / „Markér udført“), Grafik
(app-indstillinger: navn ≤ 30, bundle ID, fire farver; billedgrupper pr. butik + App-build med størrelser og „Hent PNG“),
Tekster (felter med tællere og butikkernes grænser, overskrifter ≤ 32), Links og ID'er (FORSLAG-piller, identifikatorer),
Privatliv og vurdering, Vejledning (App Store, Google Play, Expo og credentials). Grænser og tjekliste: `listing.ts` i happy-groups.

### 09–10 MCP-servere
Serverskifter Appen (`/mcp`) / Admin (`/mcp/admin`); faner Oversigt, Værktøjer, Kladder · n, Monitor, Forbind.
Katalog læses fra `specs/mcp/catalog.json`. Værktøjsrækker med tilstandsbadges (Læs grøn, Skriver blå,
Kladde → bekræft amber, Bekræfter rød) og „Deep link“. Inspektør med parametre, live deep link, Kør / Opret kladde,
mørkt resultatpanel (`#0f172a`) og kladdekort (`#fffbeb`, `#fcd34d`) med årsag + Bekræft / Annullér. Backend: `specs/mcp/SPEC.md`.

### 11 Happy AI-udvidelse
Popover 380 px under topbaren: status-pille (Forbundet / Forældet / Ikke installeret · version), aktuel fane
(`GET_PAGE_INFO`), markeret tekst (`GET_SELECTION`) med „Åbn <bruger>“ når den matcher en bruger, handlinger
(Åbn sidepanelet · Vis sidepanelet som testkonto · Åbn API-tracer · Udvidelsens indstillinger), instansvalg
(`GET_INSTANCES`/`SET_INSTANCE`) og bro-log. **Uden handshake**: PING med backoff (1, 2, 3, 5 s); fejler den,
åbner et sidepanel (440 px) med installationsguide til Chrome Web Store
`https://chromewebstore.google.com/detail/happy-ai/cpfeknokgmlbnnllagedhmiaiocflegh` — én gang pr. browser
(`localStorage tg-ext-guide-seen`), ellers ved klik på ikonet.

### 12 API-tracer
Fra happy-groups `core/tracer/TracerOverlay.tsx` + `TracerPanel.tsx`: mærke nederst til venstre (16/80 px), trækbart
(tærskel 4 px, holdes i viewporten), klik åbner panelet (420 px, højre), × spørger „Indtil siden genindlæses“ /
„Resten af denne browsersession“ (`sessionStorage`). Panel: in-flight/errors-badges, sidekontekst (side, bruger,
efterligner, udvidelse, API), Recording/Paused, vælg alle, Copy YAML, Download, rækker med metode/sti/status/tid
og udfoldning med URL og bodies.

### 13–19 Happy AI-sidepanel · udviklerkonti og Expo (i udvidelsen)
Samme mønster som `toergaer`-onboardingen i sidepanelet: tre kort (App Store · Apple Developer, Google Play Console, Expo · EAS)
med fremdrift; fire trin hver med punktliste, „Åbn …“-knap, „Du er på …“ når fanens adresse matcher, felter med
validering og til sidst et tjek af API-nøglen / servicekontoen. Felter og regler står i
`design/Happy AI Sidepanel - App Stores.dc.html` (`GUIDES`, `VALID`). Resultatet (Team ID, Issuer ID, Key ID,
Developer ID, servicekontoens e-mail, Expo-organisation og EAS Project ID) sendes til `PUT /api/admin/app-store`; `.p8` og JSON-nøgler forlader aldrig
brugerens computer via Happy Mates. Expo-guiden: konto med 2FA → organisation (bliver `owner` i `mobile/app.config.ts`) →
EAS-projekt (`extra.eas.projectId`) → robot-token, som gemmes direkte som GitHub Actions-secret `EXPO_TOKEN` i forken
med brugerens GitHub-token fra ToerGaer-onboardingen og aldrig gemmes i udvidelsen.

## Interactions & behavior
- Alt i `/admin` skrives i audit-loggen (`require_admin` logger GET; ændringer skriver `audit()` i samme transaktion).
- Overgange kun farve/baggrund ≤ 300 ms. Fokus: 1 px `#2563eb` ring.
- Alle gitre er `auto-fit` og falder til én kolonne under ~900 px. Tabeller får vandret scroll.
- Toasts: bund midt, `#0f172a`, 2,6 s.

## Backend (nye tabeller, alle via migrationer)
| Migration | Tabeller |
|---|---|
| `0005_impersonation.sql` | `users.impersonation_only`, `users.impersonation_owner_id`, `sessions.impersonator_id` |
| `0006_features.sql` | `features`, `feature_rings` (bruger-niveau), `feature_usage` (append-only) |
| `0007_app_store.sql` | `app_store` (én række: tekster pr. sprog, links, manuelle trin, ID'er) |
| `0008_mcp.sql` | se `specs/mcp/SPEC.md` |
| `0009_devices.sql` | se `specs/mobile/SPEC.md` |

Efterligning (fra happy-groups `admin/impersonation/routes.ts`): `GET/POST /api/impersonation/users`,
`POST /api/impersonation/users/presets`, `DELETE /api/impersonation/users/{id}`, `POST /api/impersonation/start`,
`POST /api/impersonation/stop`. En testkonto kan aldrig logge ind via `/api/auth/*`.

## Design tokens
- Tekst `#020817`, sekundær `#334155`/`#475569`, dæmpet `#64748b`, svag `#94a3b8`.
- Flader `#ffffff`, `#f8fafc`, `#f1f5f9`; sidebaggrund `#f1f5f9`; menu `#0f172a`.
- Kanter `#e2e8f0`, `#f1f5f9`, stiplet `#cbd5e1`.
- Primær `#2563eb`, hover `#1e58d3`, tint `#eff6ff`/`#dbeafe`.
- Succes `#16a34a`/`#22c55e`, `rgba(22,163,74,0.12)`/`#166534`. Advarsel `#f59e0b`/`#f97316`, `#fffbeb`, `#fef3c7`, `#fcd34d`,
  `#92400e`, `#78350f`. Fejl `#dc2626`, `rgba(220,38,38,0.1)`/`#b91c1c`.
- Glød på fremhævede kort: `linear-gradient(90deg,#f97316,#f59e0b)`, blur 10–12 px, opacity 0.22–0.3.
- Radier 6 · 8 · 10 · 12 · 16 · 9999. Afstande 4 · 6 · 8 · 10 · 12 · 14 · 16 · 20 · 24 · 28.
- Type: Inter (`--hm-font-sans`), mono `--hm-font-mono`; 10 · 11 · 12 · 13 · 14 · 15 · 17 · 18 · 22 · 26 · 30 px.
- Fra `design/_ds/…/colors_and_type.css` (Happy Mates Design System): `--hm-brand-gradient`, `--hm-shadow-*`, `hm-rainbow`.

## Assets
- `design/assets/happy-mates-logo.svg` — Happy Mates-mærket (udvidelsesknap, guides). Brug repoets eget, hvis det findes.
- Tørgær-mærket er tegnet i `design/ToergaerMark.dc.html` (SVG, varianter a/b/c).
- Ikoner: lucide `0.562.0` (`lucide-react` i `web/`).

## Files
- `design/ToerGaer Admin.dc.html` — hele admin-appen (shell, sektioner, udvidelse, tracer)
- `design/Happy AI Sidepanel - App Stores.dc.html` — sidepanelets guides: Apple Developer, Google Play Console, Expo · EAS
- `design/ToergaerMark.dc.html`, `design/support.js`, `design/_ds/…`, `design/assets/…` — kun for at prototypen kan køre
- `screenshots/` — 19 skærmbilleder (se tabellen ovenfor)
- Relaterede specs: `specs/mcp/` (MCP-servere), `specs/mobile/` (mobil-app)
- AI-instruktioner: `.gaer/agents/instructions.md`, skills `gaer-backend`, `gaer-mcp`
