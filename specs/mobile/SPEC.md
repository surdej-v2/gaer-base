# Gær mobil-app — `mobile/`

Reference: `happy-mates-apps/happy-groups` → `apps/native/` (Expo, React Native, `src/auth`, `src/shell/AppScreen.tsx`,
`src/bridge/protocol.ts`, `src/links`). Port the shape, drop everything about brands.

## Not in Gær (white-label)
No `brands/` folder, no `APP_BRAND`, no per-brand EAS profiles, no theme switch, no `generate_brand_assets.py`,
no "two installs on one phone". One fork = one app. Name, icon and colours come from the instance settings.

## App
- Expo (same SDK line as happy-groups), TypeScript, `expo-router` not needed — a stack of 5 screens.
- `mobile/app.config.ts` reads `mobile/app.json` + env:
  | Env | What |
  |---|---|
  | `GAER_ORIGIN` | `https://<domæne>` — API, web-app and RP ID host |
  | `GAER_APP_NAME` | ≤ 30 chars, from /admin → Indstillinger → navn |
  | `GAER_BUNDLE_ID` | e.g. `dk.<kunde>.app`, same on iOS and Android |
  | `GAER_SCHEME` | URL scheme, e.g. `gaer-<kunde>` |
  | `EAS_PROJECT_ID`, `APPLE_TEAM_ID` | build / signing |
- `associatedDomains`: `applinks:<domæne>` and `webcredentials:<domæne>` (passkeys). Android `intentFilters` with `autoVerify`.
- Icons and splash: `mobile/assets/{icon,adaptive-icon,monochrome-icon,splash}.png`, produced from /admin → App Stores → Grafik.
- Tablets: iOS `supportsTablet: true`, Android tablets rotate, phones portrait.

## Screens
| # | Screen | Calls |
|---|---|---|
| 1 | Velkommen | — (passkey button if one is stored) |
| 2 | Telefonnummer | `POST /api/auth/otp/start` |
| 3 | SMS-kode | `POST /api/auth/otp/verify` → `scope: full` or `enroll` |
| 4 | Passkey | `/api/auth/passkey/register/{options,verify}` (enroll) · `/api/auth/passkey/login/{options,verify}` |
| 5 | App | WebView of `<origin>/m#handoff=<code>` |

Login rules are the web's (skill `gaer-auth`): no PIN, no dev bypass. The native client sends
`X-Gaer: 1` and `X-Gaer-Client: mobile`. The session token is kept in `expo-secure-store`.

## New backend endpoints (`server/app/routes/`)
| Endpoint | What |
|---|---|
| `POST /api/auth/handoff` | full session → single-use code (60 s, hashed) |
| `GET /m#handoff=<code>` | the PWA posts it to `POST /api/auth/handoff/redeem` → session cookie for the WebView |
| `POST /api/me/devices`, `DELETE /api/me/devices` | Expo push token per install (new table `devices`, in GDPR export) |
| `GET /.well-known/apple-app-site-association`, `/.well-known/assetlinks.json` | rendered from settings `app_store` (team id, bundle id, Android SHA-256) |
| `GET/PUT /api/admin/app-store` | the App Stores section's data (texts, links, manual checks). Audited |

All new tables: migrations, `audit()` on change, GDPR export + erasure.

## WebView bridge (`mobile/src/bridge/protocol.ts` ≡ `web/src/bridge/native.ts`)
`auth:logout` (sign out natively) · `auth:expired` (new handoff + reload) · `badge:set` (unread count) ·
`open:external` (system browser) · `camera:open` (native picker for /m upload when the WebView lacks it).
Namespace `gaer-native`, same request/response shape as `happyai-bridge`.

## Push
`expo-notifications`; token → `POST /api/me/devices`. The server sends through Expo's push API
(`server/app/push.py`, `EXPO_ACCESS_TOKEN`). A tap opens the notification's `path` in the WebView.

## Builds (EAS)
- Onboarding: Happy AI-sidepanelet → App Stores → Expo · EAS (konto, organisation, projekt, robot-token → `EXPO_TOKEN`).
- `mobile/app.config.ts`: `owner` = Expo-organisationen, `extra.eas.projectId` = EAS Project ID (fra `/admin` → App Stores).
- Profiles: `development` (dev client), `preview` (APK / ad-hoc), `production` (AAB / App Store).
- `.github/workflows/mobile.yml`: on PR typecheck + lint + tests; manual `eas build` + optional `eas submit`.
- Store credentials live in EAS (`eas credentials`), never in the repo.
- `runtimeVersion` follows `version`; OTA only to the same version.

## Verify
- Phone → code → full session on grace logins 1–3; 4th login requires a passkey (same as web).
- Handoff code works once, expires after 60 s, never appears in a URL query.
- WebView stays on the instance's domain; other links open externally.
- `pytest server` covers handoff, devices and well-known; `npm test --prefix mobile` covers phone parsing and deep links.
