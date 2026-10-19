# Handoff: Gær mobil-app (iOS + Android)

Én Expo-app i `mobile/` til kundens instans — **ikke white-label**. Hver fork bygger sin egen app med sit
eget navn, ikon, bundle-id og domæne; der er ingen brand-filer, ingen brand-skifter og ingen fælles
build af flere apps. Modellen er taget fra happy-groups `apps/native` (D-HG-18: appen logger kun ind,
resten er web-appen i én WebView), tilpasset Gær.

| Fil | Hvad |
|---|---|
| `README.md` | Denne fil |
| `SPEC.md` | App, login, WebView-bro, push, deep links, builds, backend-endpoints, verify |
| `CLAUDE_CODE_PROMPT.md` | Startprompt til Claude Code |

Admin-delen er sektionen **App Stores** i den selvstændige /admin-app (`ToerGaer Admin.dc.html`):
overblik med tjekliste pr. butik, grafik, tekster med tegngrænser, links og ID'er, privatliv og
vurdering, og vejledninger. Uden whitelabel-delen (ingen "Nyt brand", ingen ikonforslag pr. brand,
ingen brand-fil).

## Flow
1. **Velkommen** → **Telefonnummer** → **SMS-kode** (samme regler som web: tre grace logins, så passkey)
   → **Passkey** (Face ID / fingeraftryk) når grace er brugt op.
2. Efter login: `/m` (den eksisterende PWA med bundmenu, kamera-upload og klip) i én WebView. Sessionen
   overføres med en engangskode i `#handoff=` — aldrig i query string.
3. Links uden for instansens domæne åbnes eksternt. Android-tilbage går først i WebView-historikken.
