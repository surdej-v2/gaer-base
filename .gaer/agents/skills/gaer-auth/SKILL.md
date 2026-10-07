---
name: gaer-auth
description: Login in Gær — phone number, one-time code, three grace logins, then passkey. Use when touching login, sessions, users or anything that decides who may do what. These rules must not be bypassed.
---

# Login (gaer-auth)

Code: `server/app/routes/auth.py`, `server/app/sessions.py`, `server/app/security.py`, `web/src/pages/Login.tsx`, `web/src/auth.tsx`.

## The rules
1. **Phone number is the identity.** Normalized to E.164 (`normalize_phone`, default `+45`). Stored in `users.phone`.
2. **One-time code by SMS.** 6 digits, stored only as HMAC (`otp_codes.code_hash`), expires after `OTP_TTL_SECONDS`
   (300), single use. A new code invalidates older ones. Max `OTP_MAX_ATTEMPTS` (5) failures per number within
   `OTP_LOCK_MINUTES` (15) locks the number. Max 5 codes per number and 30 per IP per hour.
   `otp/start` answers the same whether or not the number exists or is blocked.
3. **First admin.** With `BOOTSTRAP_ADMIN_PHONE` set, only that number becomes admin, and only while the instance
   has no admin (an existing normal user with that number is promoted, `role.changed`); everybody else becomes a
   normal user. Without it, the very first user becomes admin. Decided in `_get_or_create_user` under an advisory
   lock; `admin.first_user` is audited. `admin_open_to_anyone()` drives the "you become admin" notice on `/w` and
   `/login` — it is false when the bootstrap number is set, so a fresh instance is not advertised.
4. **Three grace logins.** Without a passkey and with `grace_logins_used < GRACE_LOGINS` a code gives a
   **full** session and increments `grace_logins_used` (`grace.used`). The user is offered a passkey each time.
5. **Then passkey is required.** When grace is used up, or the user already has a passkey, a code gives an
   **enroll** session that can only call `/api/auth/passkey/register/*`. Registering upgrades it to full.
   A new device / lost passkey uses this path and does **not** consume a grace login.
6. **Passkey login** (`/api/auth/passkey/login/*`) is discoverable (no username), verifies sign count, refuses blocked users.

## Sessions
- Random token in the `gaer_session` cookie (HttpOnly, Secure on https, SameSite=Lax). Only its SHA-256 is stored.
- Embedded (browser extension / Office iframe, header `X-Gaer-Embedded: 1`): SameSite=None; Partitioned.
- Dependencies: `any_session` (enroll or full — passkey registration only), `current_user` (full),
  `require_admin` (full + admin role; logs `admin.read` on GET).
- Blocking a user revokes all sessions. Logout revokes the current one. All revocations log `session.revoked`.

## Never
- Add a login path that skips the code or the passkey rules, or give an enroll session access to anything else.
- Store codes, tokens or full phone numbers in logs (logs hold `phone_masked` + `phone_hash`).
- Return different answers from `otp/start` for known/unknown numbers.
- Accept state-changing requests without the `X-Gaer: 1` header (CSRF, enforced in `main.py`).

## Tests
`server/tests/test_auth.py` uses a software authenticator (`tests/helpers.py`) — extend it for any change.
