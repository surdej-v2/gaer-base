---
name: gaer-auth
description: Login in Gær — e-mail (or SMS) one-time code, passkeys, security keys and authenticator apps; MFA for /admin after three grace logins, MFA opt-in for users. Use when touching login, sessions, users, MFA or anything that decides who may do what. These rules must not be bypassed.
---

# Login and MFA (gaer-auth)

Code: `server/app/routes/auth.py` (login + second factor), `server/app/sessions.py` (sessions, guards),
`server/app/mfa.py` (factor rules), `server/app/totp.py` (authenticator apps), `server/app/routes/security.py`
(the user's own factors), `server/app/security.py`, `web/src/auth.tsx`, `web/src/pages/Login.tsx`.
Frontend usage (components, step-up dialog, profile API): skill `gaer-profile`.

## The rules
1. **E-mail is the identity** (normalized, lower case, `users.email`). With `SMS_LOGIN=1` a phone number (E.164,
   default `+45`, `users.phone`) works the same way.
2. **One-time code.** 6 digits, stored only as HMAC (`otp_codes.code_hash`), expires after `OTP_TTL_SECONDS`
   (300), single use. A new code invalidates older ones. Max `OTP_MAX_ATTEMPTS` (5) failures per contact within
   `OTP_LOCK_MINUTES` (15) locks it. Max 5 codes per contact and 30 per IP per hour.
   `otp/start` answers the same whether or not the contact exists or is blocked.
3. **First admin.** With `BOOTSTRAP_ADMIN_EMAIL` (or `BOOTSTRAP_ADMIN_PHONE` with SMS) only that contact becomes
   admin, and only while the instance has no admin. Without it, the very first user becomes admin. The first admin
   is the **super admin**. Decided in `_get_or_create_user` under an advisory lock; `admin.first_user` is audited.
4. **Second factors** (`app.mfa`): passkeys (platform), **security keys** (YubiKey …, `passkeys.kind =
   'security_key'`) and **authenticator apps** (TOTP, `totp_factors`, confirmed with a code). Registration requires
   user verification (PIN/biometrics). A WebAuthn assertion only counts as a second factor when the authenticator
   **verified the user** (UV flag, checked server-side with `require_user_verification=True`).
5. **Everybody is offered a passkey, nobody is forced** — except:
   - **/admin**: an admin without a second factor gets `GRACE_LOGINS` (3) **grace logins** with a code
     (`grace.used`, `sessions.grace`). A grace session may enter /admin for `ADMIN_MFA_HOURS` (12). After that, or
     when the admin has a factor, `require_admin` needs a second factor proven within `ADMIN_MFA_HOURS`
     (`sessions.mfa_at`) → `403` + `X-Gaer-Error: mfa_required` / `mfa_setup_required`.
   - **Security actions** in /admin (roles, e-mail/phone, block/unblock, reset passkeys/MFA, revoke sessions,
     GDPR erasure, purging a user's logs) use `require_admin_stepup`: a second factor within `STEPUP_MINUTES` (15).
     A grace login is **not** enough → `X-Gaer-Error: stepup_required`.
   - **"Kræv passkey"** (`users.mfa_required`, the user's opt-in): a code (or a passkey without UV) only gives a
     pending **`mfa`** session that can call nothing but `/api/auth/mfa/*`. It fails closed: with no factor left
     the user stays pending until an admin resets MFA.
6. **Step-up** (`/api/auth/mfa/passkey/*`, `/api/auth/mfa/totp/verify`) always revokes the current session and
   issues a **new token** with `mfa_at = now()` — a pending or code-only session is never upgraded in place.
   Passkey step-up uses the user's own credentials (`allowCredentials`), so non-discoverable security keys work.
   TOTP: ±1 step, replay blocked by a conditional update of `last_used_step`, wrong codes lock like one-time codes.
7. **Changing factors** (add/remove passkey, key or app; switch "Kræv passkey" on/off) — `mfa.ensure_can_change_factors`:
   the **first** factor may be added right after a code login (bootstrap). Once the user has one, a second factor
   within `STEPUP_MINUTES` is required, so a stolen code cannot add the attacker's passkey or lock the owner out.
   The user row is locked while factors change. The last factor cannot be removed while "Kræv passkey" is on.
   Adding the first factor revokes the user's other grace sessions. The same recent second factor
   (`mfa.require_recent_mfa`) is needed to change one's own e-mail or phone and to delete one's account.
8. **Recovery** is an admin action: `POST /api/admin/users/{id}/reset-mfa` (deletes keys and apps, switches
   "Kræv passkey" off, logs the user out). Only the super admin may reset another admin; nobody resets themselves.
9. **Passkey login** (`/api/auth/passkey/login/*`) is discoverable (no username), verifies sign count, refuses
   blocked users; with UV it counts as MFA.
10. TOTP secrets are encrypted with AES-GCM using versioned keys (`TOTP_KEYS`, fallback derived from `SECRET_KEY`)
    and never leave the server after setup; the GDPR export lists apps without secrets.
11. **Sign-up** (`app/signup.py`, setting `signup`, /admin/settings → Tilmelding): `open` (default), `domains` (e-mail
    domains and their subdomains) or `closed`. A contact without an account that may not sign up gets **no code**, and
    `otp/start` answers exactly as usual (auth log `otp.sent` fail, reason `signup_closed`). `_get_or_create_user`
    checks again, so a code sent before the setting changed creates no account. The first admin (bootstrap contact,
    or the first user on a new instance) always gets in. Admins create users with `POST /api/admin/users`
    (`users.created_by`); an admin only with role `fuld`, and an admin or a user with the YubiKey requirement needs a
    step-up.
12. **Login link**: a login code sent by e-mail also has a link `ORIGIN/login#link=<token>` (only the token's SHA-256 is
    stored, `otp_codes.link_hash`). `POST /api/auth/otp/link` is the same as typing the code: single use, consumes the
    code too, expires with it, then the usual second-factor rules. The token sits in the fragment and /login finishes
    the login only on a click (mail scanners). "Gendan adgang" in /admin (`POST /api/admin/users/{id}/login-link`)
    sends the same mail, optionally after a reset-mfa (step-up, same rules). There are no passwords to reset.
13. **YubiKey requirement** (`users.yubikey_required`, set in /admin/users, step-up): `require_admin`,
    `require_admin_stepup`, `mfa.stepup_ok` / `mfa.require_stepup` (also /forbind `mcp:admin`, Beskeder to everyone,
    mail) count only a YubiKey proven in this session (`sessions.yubikey_at`), within `ADMIN_MFA_HOURS` /
    `STEPUP_MINUTES`; no grace logins. Codes `yubikey_required`, `yubikey_setup_required`, `yubikey_stepup_required`.
    A key is a YubiKey by its attestation (`app/yubikey.py`: packed/fido-u2f, issuer Yubico; with `YUBICO_ROOTS` the
    chain must end in Yubico's root → `attestation = 'verified'`). It counts (`passkeys.yubikey`) only when an admin
    enrolled it — in person (`/api/admin/users/{id}/yubikey/*`, non-discoverable, `enrolled_by`; such a key is
    **never a first factor**, passkey login refuses it; only the super admin may do this for another admin), set up by
    the **super admin alone and sent** to the user (same calls with `mode: "ship"`, `passkeys.shipped_at`, optional mail
    that it is on its way, never the PIN; same second-factor-only rule; first use logged as `yubikey.first_use`), or with a
    single-use link (`yubikey_enrollments`, `/konto#yubikey=<token>`, `/api/auth/yubikey/enroll/*`) — or when the
    user proved a YubiKey in the last `STEPUP_MINUTES`. Users without the requirement simply have their YubiKeys
    recorded as such. Nobody switches the requirement on for themselves without a YubiKey.

14. **Login providers** (`app/login_providers.py`, `app/saml.py`, `routes/login_providers.py`, `/admin/login-providers`,
    role Drift): Microsoft Entra ID and Google (OpenID Connect, code + PKCE + nonce), GitHub and Amazon (OAuth 2.0 + PKCE)
    and SAML 2.0 connections. Each is **off until an admin switches it on** (step-up, audited, webhook `auth.provider.*`).
    - **A provider replaces only the one-time code.** After it, `_after_first_factor` decides exactly as after a code:
      "Kræv passkey" → pending `mfa` session; admins use grace logins and then need a second factor for /admin; a
      passkey is offered. A provider never sets `mfa_at`, never counts as a step-up and **never makes anyone admin**
      (new users are always `user`; bootstrap/first-user promotion only happens with a code).
    - **Which account**: an identity is the provider's stable subject (`user_identities`, unique per provider and per
      user). The first time it is linked on an e-mail the provider verified (setting `login_providers.link =
      verified_email`) or only by the user from `/konto` (`user_linked`; linking/unlinking needs `mfa.require_recent_mfa`).
      Entra and SAML e-mails count as verified only on the provider's listed domains; Amazon's never.
    - **New people** only when the provider (`new_users = signup`), the rules (`new_users = signup`) and Tilmelding
      (`app/signup.py`) all allow it, and the e-mail is verified.
    - **Flows** (`login_flows`) are bound to the browser by the `gaer_login_flow` cookie (hash stored), single use (also
      after a refusal), expire in 10 minutes. SAML: assertion signature always required (signxml, only the signed element
      is read), destination/recipient/audience/time window/InResponseTo checked, assertion ids remembered against replay,
      IdP-initiated only when allowed; XML parsed without entities or network. The ACS is CSRF-exempt (a form the IdP posts).
    - **Required provider for a domain** (rule `required`): `otp/start` sends no code to that domain (same answer, auth
      log `otp.sent` reason `provider_required`) and `otp/verify` refuses it — except for the **super admin** (emergency access).
    - Client secrets and SAML SP keys are encrypted with `app.secrets_box`, write-only in the API, never logged or in webhooks.
    - Auth log: `provider.login`, `provider.linked`, `provider.unlinked` (detail `provider`, `reason`).

## Administrative roles (`server/app/admin_roles.py`)
An admin (`users.role = 'admin'`) also has one or more **admin roles** in `users.admin_roles`. They decide which parts
of /admin the admin may use; they never widen anything a plain admin could not do before.

| Role | Opens |
| --- | --- |
| `fuld` Fuld adgang | everything — and only it may change who is admin and their roles, change another admin's account, or use `/mcp/admin` |
| `support` | Brugere og roller, Efterligning, Auth-log |
| `indhold` | Website, Filer, App Stores |
| `produkt` | Features og ringe, AI, Analytics, Heatmaps |
| `data` | Data: Katalog, Udforsk, Forbind API |
| `privatliv` | GDPR, Samtykke og cookies, Sletning af logdata, Audit-log, Auth-log |
| `drift` | Login-udbydere, MCP-servere, Udvidelse og Office, Indstillinger |
| `udvikler` | Gær Developer (`/developer`, `/api/admin/dev`, `/api/admin/studio`): drafts of datasets, pipelines, operations, endpoints, APIs |
| `udvikler_admin` | as `udvikler`, plus connections, API keys, known instances, imports and giving the role `udvikler` (`DEV_ADMIN_WRITES`). External users can never hold either role (trigger `users_developer_roles`); see `specs/developer/ROLES.md` |

- Statistik (`/admin`) is open to every admin. Several roles add up.
- One table (`SECTIONS` + `ROLE_SECTIONS`) drives both the menu (`/api/auth/me` → `admin_sections`) and the check in
  `require_admin` (path + method). An `/api/admin` path no section names is `fuld` only — **add new sections there**.
- A database trigger keeps the rules: new admins get `fuld`, users have no roles, the super admin always has `fuld`.
  `PUT /api/admin/users/{id}/admin-roles` (step-up, `fuld` only) refuses removing the last `fuld` admin. Changes are
  audited as `role.changed` with `admin_roles` before/after.

## Sessions
- Random token in the `gaer_session` cookie (HttpOnly, Secure on https, SameSite=Lax). Only its SHA-256 is stored.
- Scopes: `full`, and `mfa` (pending second factor, `MFA_SESSION_MINUTES`). Columns `mfa_at`, `grace`.
- Embedded (browser extension / Office iframe, header `X-Gaer-Embedded: 1`): SameSite=None; Partitioned.
- Dependencies: `any_session` (full or pending — **only** `/api/auth/mfa/*`), `current_user` (full),
  `require_admin` (full + admin role + MFA/grace; logs `admin.read` on GET), `require_admin_stepup` (fresh MFA).
- Blocking a user revokes all sessions. Logout revokes the current one. All revocations log `session.revoked`.

## Never
- Send a code to, or create, a contact that may not sign up, or answer `otp/start` differently for it.
- Count a passkey, an app or a non-YubiKey key where the YubiKey requirement applies, or let a key an admin enrolled
  in person log a user in on its own.
- Add a login path that skips the code other than a login provider an admin switched on (rule 14), or a way to get a
  full session from a pending one without a second factor. Let a provider count as a second factor or give the admin role.
- Count a WebAuthn assertion without verified UV as MFA, or upgrade a session row in place instead of a new token.
- Let a code-only session add a factor to an account that already has one, or accept a grace login for a
  security action.
- Store codes, tokens, TOTP secrets or full contacts in logs (logs hold `phone_masked` + `phone_hash`).
- Return different answers from `otp/start` for known/unknown contacts.
- Accept state-changing requests without the `X-Gaer: 1` header (CSRF, enforced in `main.py`).

## Tests
`server/tests/test_auth.py` and `test_mfa.py` use a software authenticator (`tests/helpers.py`: `SoftAuthenticator`
with `uv=` flag, `stepup`, `totp_code`) — extend them for any change.
