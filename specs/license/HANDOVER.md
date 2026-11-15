# Handover: the Gær license backoffice in Surdej (happymates.ai)

**For:** whoever builds the license backoffice in Surdej (person or agent).
**From:** gaer-base, where the checking side is built and merged on branch `claude/upbeat-archimedes-wcttui`.
**Owner of the licenses:** Happy Mates Services ApS.

## 1. Why

Gær is proprietary (see `LICENSE` in gaer-base). Every customer runs their own fork. To make each installation
visible and checkable, happymates.ai issues one **license key** per customer instance. Gær verifies the key on
the server and puts a signed **license statement** with the **license id** into the `<head>` of every public
website page (`/w/…`). Without a valid key the pages say the installation is UNLICENSED.

Gær is done. What's missing is the issuing side: **a backoffice in Surdej to manage signing keys, issue, renew
and revoke licenses, publish a verification page, and monitor the installations.** This document is the contract
between the two sides; anything in section 2 is fixed by Gær's code and must be produced exactly.

## 2. The contract (what Gær checks today)

Code: `server/app/license.py`, tests: `server/tests/test_license.py` in gaer-base.

### 2.1 Token format

A compact JWS (JWT), three base64url parts **without padding**: `header.payload.signature`.
Signature: **Ed25519** over the ASCII bytes `base64url(header) + "." + base64url(payload)`.
Any JOSE library with EdDSA works (Python `cryptography`/PyJWT, npm `jose`). Max length 8192 bytes.

Header:

```json
{ "alg": "EdDSA", "typ": "JWT", "kid": "hm-2026-1" }
```

- `alg` must be exactly `EdDSA`; anything else (`none`, `HS256`, `RS256`, `ES256`) is rejected.
- `kid` must be a key id present in Gær's `ISSUER_KEYS` (section 2.3).

Payload (claims):

```json
{
  "iss": "https://happymates.ai",
  "prd": "gaer",
  "jti": "HM-2026-0001",
  "sub": "12345678",
  "domains": ["app.kunde.dk", "www.kunde.dk", "*.kunde.net"],
  "lic": { "name": "Kunde ApS", "cvr": "12345678" },
  "stmt": "Gær © Happy Mates Services ApS. Licens HM-2026-0001 udstedt af happymates.ai til Kunde ApS (CVR 12345678). Alle rettigheder forbeholdes.",
  "iat": 1794380000,
  "nbf": 1794380000,
  "exp": 1825916000
}
```

| Claim | Required | Rule in Gær |
| --- | --- | --- |
| `iss` | yes | exactly `https://happymates.ai` |
| `prd` | yes | exactly `gaer` (other products later get their own value) |
| `jti` | yes | the **license id**, non-empty string. Shown publicly. Format we use: `HM-<year>-<4 digits>` |
| `domains` | yes | **JSON array** of strings, non-empty. A string instead of an array is rejected |
| `exp` | yes | **integer** Unix seconds (a float is rejected). 5 min leeway |
| `nbf` | no | integer; if absent `iat` is used. 5 min leeway |
| `iat` | no | integer |
| `lic` | no (send it) | `{ "name": string, "cvr": string }` — used to build a statement when `stmt` is missing |
| `stmt` | no (send it) | the statement, published **verbatim** (HTML-escaped by Gær). Keep it one line, under ~400 chars |
| `sub` | no | informational; we use the CVR |

Unknown claims are ignored, so the backoffice may add e.g. `plan` or `agr` (agreement ref) without a Gær change.

### 2.2 Domain matching

- Case-insensitive, port ignored, compared with the `Host` of each request **and** with the host of the
  instance's `ORIGIN` (that one decides what `/health` says).
- `app.kunde.dk` matches only `app.kunde.dk`.
- `*.kunde.net` matches any subdomain at any depth (`www.kunde.net`, `a.b.kunde.net`) but **not** `kunde.net`
  itself; list both if both are used.
- No other wildcards.

The backoffice must validate domains when they are entered (hostname syntax, lowercase, no scheme, no path, no
port, `*.` only as a prefix) and should warn if the customer's ORIGIN domain is not in the list.

### 2.3 Public keys live in Gær's code

`ISSUER_KEYS` in `server/app/license.py`: `kid → raw 32-byte Ed25519 public key, base64url without padding`.
It is **empty today**, so every installation currently reports UNLICENSED. Keys are code, not configuration,
on purpose: a fork cannot point the check at its own key without a visible diff, which is a breach of LICENSE §2(h).
Getting a key in means a PR to gaer-base; forks get it when they pull updates.

### 2.4 Statuses Gær reports

`valid` · `missing` (no `LICENSE_KEY`) · `invalid` (bad signature, unknown kid, wrong alg/iss/prd, no jti, no exp,
no/empty domains array, unreadable) · `expired` · `not_yet_valid` · `wrong_host` (valid key, domain not listed).

### 2.5 What an installation exposes (what the monitor reads)

On every `text/html` response under `/w` (including 404s), right after `<head>`:

```html
<!-- <statement> Licens-id: HM-2026-0001. Status: valid. -->
<meta name="license" content="HM-2026-0001">          <!-- "UNLICENSED" when not valid -->
<meta name="license-status" content="valid">           <!-- one of the statuses above -->
<meta name="copyright" content="<statement>">
<link rel="license" href="https://happymates.ai/licenses/HM-2026-0001">   <!-- only when valid -->
```

Response header: `Gaer-License: HM-2026-0001; status=valid` (or `UNLICENSED; status=<status>`).

`GET https://<domain>/health` returns, among other things:

```json
{ "ok": true, "version": "…", "license": { "status": "valid", "id": "HM-2026-0001",
  "expires_at": 1825916000, "domains": ["app.kunde.dk", "www.kunde.dk", "*.kunde.net"] } }
```

### 2.6 Reference implementation

gaer-base ships a CLI that produces exactly this format. Use it as the reference and in tests:

```bash
cd server && python -m app.license keygen --kid hm-2026-1 --out private.pem
python -m app.license issue --key private.pem --kid hm-2026-1 --id HM-2026-0001 --name "Kunde ApS" --cvr 12345678 --domain app.kunde.dk --domain www.kunde.dk --days 365
python -m app.license verify <jwt> --host app.kunde.dk
```

## 3. What to build in Surdej

### 3.1 Signing keys

- Generate Ed25519 key pairs in the backoffice; the private key lives only in Surdej's secret store
  (encrypted at rest; never shown, exported or logged). Optional: an offline/HSM key, with Surdej holding only
  the public half and a signing step.
- Each key has a `kid` (`hm-<year>-<n>`), created/retired dates and a state: `pending` (created, not yet in
  gaer-base) → `active` (in gaer-base `ISSUER_KEYS`, may sign) → `retiring` (no new signatures, still in
  `ISSUER_KEYS` while its licenses run) → `retired` (removed from gaer-base).
- The UI shows the exact line to paste into `ISSUER_KEYS` (`"hm-2026-1": "<base64url>",`) and, ideally, opens the
  PR to gaer-base. Signing is blocked while a key is `pending`.
- Rotation: create a new key → PR to gaer-base → wait until customers have pulled (the monitor in 3.5 shows which
  installations already accept the new kid: re-issue to one and check its `/health`) → sign new licenses with it →
  retire the old one when no running license uses it.

### 3.2 License registry

One row per license (the `jti`), with history of every token issued for it:

- **License:** id (`HM-2026-0001`, sequential per year, never reused), customer (link to Surdej's customer/CVR
  record), licensee name and CVR as signed, domains (array), product (`gaer`), plan/notes, agreement reference
  (the written agreement LICENSE §1.1 requires — a license without one should not be issuable), valid from/to,
  status (`draft`, `active`, `suspended`, `revoked`, `expired`), statement text, contact for renewals.
- **Issued tokens:** license id, kid, iat, nbf, exp, domains and statement as signed, who issued it, a hash of
  the token (the token itself is not secret and can be stored and shown again).

### 3.3 Issuing, renewing, changing

- Create → fill fields → preview the statement exactly as it will appear in the `<head>` → sign → show the token
  with copy and a "how to install" text: set `LICENSE_KEY=<token>` in the customer's `deploy/app.env` and
  redeploy; check `https://<domain>/health`.
- The statement is generated from a template (default below, Danish, identical to Gær's fallback) and may be
  edited per license:
  `Gær © Happy Mates Services ApS. Licens {id} udstedt af happymates.ai til {name} (CVR {cvr}). Alle rettigheder forbeholdes.`
- Renewal or a change of domains/statement issues a **new token with the same `jti`**; the old token keeps
  working until its `exp` (see 3.6).
- Default term: 12 months, aligned with the agreement; reminders 60/30/7 days before `exp`.

### 3.4 Public verification page

`https://happymates.ai/licenses/<id>` — Gær links here from every licensed page. Public, no login, shows: license
id, licensee name and CVR, product, domains, valid from/to, status (active / expired / revoked / suspended) and
"issued by Happy Mates Services ApS". No personal data (contact persons, e-mails) on it.
Also `…/licenses/<id>.json` with the same fields. Unknown ids: 404 with a short "not a license issued by
happymates.ai" text.

### 3.5 Compliance monitor

A scheduled job (daily) per active license and domain:

1. `GET https://<domain>/w/` — read `meta[name=license]`, `meta[name=license-status]` and the `Gaer-License`
   header. Non-wildcard domains only; for `*.x` check the domains seen in practice.
2. `GET https://<domain>/health` — read `license.status`, `license.id`, `version`.
3. Store the result (time, domain, http status, license id seen, status seen, version) and show the history per
   license.
4. Alert (mail to the Happy Mates admins) when: the id seen differs from the license, status is not `valid`,
   the page is unreachable several days in a row, or `exp` is near.

The monitor only reads public pages. It doesn't find unlicensed copies on unknown domains; a manual
"check a URL" box in the backoffice that runs the same probe on any address is useful for that.

### 3.6 Revocation (needs a decision)

A signed JWT cannot be revoked by happymates.ai alone — Gær checks it offline. Until Gær gets a revocation
check, revoking means: mark the license revoked in the registry (the public page then says revoked), stop
renewing, and rely on `exp`. Therefore keep terms short enough (≤ 12 months).
If stronger revocation is wanted, Gær can later fetch a signed list (e.g. `https://happymates.ai/licenses/revoked.jwt`,
signed with the same keys, listing revoked `jti`s) as a background job and treat a listed id as `revoked`. That is
a change in gaer-base, not in Surdej; ask for it as a separate task.

### 3.7 Access and audit

- Only Happy Mates admins with a dedicated role (e.g. "Licenser") may create keys, issue, renew, revoke.
  Creating or retiring a signing key needs a fresh second factor.
- Every action is audited in the same transaction (who, what, license id, kid, before/after). Private keys never
  appear in logs, audit entries or API responses.

## 4. Acceptance checks

- A token issued by the backoffice passes `python -m app.license verify <jwt> --host <domain>` in gaer-base (with
  the backoffice's public key in `ISSUER_KEYS`), with `"status": "valid"`, the right id, domains and statement.
- The same token with `--host` set to a domain not listed gives `wrong_host`; one past its `exp` gives `expired`.
- `exp`, `nbf`, `iat` are integers; `domains` is an array even with one domain; the header has `kid`.
- Statements with `<`, `&`, `"` and `--` survive (Gær escapes them; the backoffice must not pre-escape).
- The public page and JSON exist for every issued id and show revoked/expired correctly.
- The monitor flags a test instance with no `LICENSE_KEY` (`UNLICENSED; status=missing`) within one run.

## 5. Open decisions for Happy Mates

1. Where the private key lives: Surdej secret store, or offline/HSM with a signing step.
2. Term and renewal: 12 months with manual renewal, or shorter with automatic re-issue.
3. Whether to add the revocation list to Gær (3.6).
4. Whether other products (Surdej itself, other forks) get licenses the same way — then `prd` distinguishes them
   and the registry should not be Gær-only.
5. Text of the public verification page, Danish and English.
