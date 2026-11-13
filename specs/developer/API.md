# API-studie og flytbarhed (fase 6)

Code: `app/devkit/apis.py`, `app/routes/gaer_api.py` (the public `/gaer/...` routes), `app/routes/developer_api.py`
(`/api/admin/dev/apis`, `/api-keys`, export/import), `app/devkit/portability.py`, `web/src/developer/ApiStudie.tsx`,
migration 0035.

| Req | How |
| --- | --- |
| API-1 | `/gaer/v<major>/<slug>/<sti>`, outside Gær base's own `/api` (no cookies; CSRF does not apply). In `web/src/routes.json` as the server route `/gaer/{path}` |
| API-2 | An API operation points to an operation or a multisource source — the same definition as an MCP tool. "Kode" operations (handlers in code) are not built |
| API-3 | GET reads with the operation's parameters as query filters, its sort, and `limit`/`offset` paging. POST takes the parameters as a JSON body (searches). PUT, PATCH and DELETE answer 405 until code operations exist |
| API-4 | Yellow and red operations need a key whose scopes name exactly that operation (`POST /lejemaal/soeg`, no wildcard); a POST to them needs an `Idempotency-Key` (the answer is kept a day; a repeat answers with `Idempotent-Replayed: true`) |
| API-5 | `openapi.json` (OpenAPI 3.1) per major with fields, `x-gaer-classification`, `x-gaer-masking` and examples; Dokumentation renders it; Python and TypeScript clients are generated from it |
| API-6 | API keys (`Authorization: Bearer gk_<id>.<secret>`), OAuth 2.0 client credentials (`POST /gaer/oauth/token`, keys of type client_credentials, 60-minute tokens `gt_…`), or a user's OAuth token from the normal Gær login (the MCP OAuth) |
| API-7 | Only Udvikler-admin and Fuld create, rotate and revoke keys (step-up). A key has scopes, expiry, owners (an `api_key` artifact with RACI) and an optional IP range; only a hash is stored and it is shown once; rotation creates a new key and lets the old run for `overlap_days` |
| API-8 | User tokens: external users only reach white operations; yellow and red need a key |
| API-9 | The major is in the URL. A new major gets its own line in `dev_api_majors`; the previous one gets `sunset_at` = publish + `sunset_months` (at least 6). Old majors answer with `Deprecation` and `Sunset`, then 410. Key owners get a message at publish and 90, 30 and 7 days before (job `developer.api_sunset`). Accountable can extend; shortening is not offered |
| API-10 | Testkonsol: calls an operation as yourself or a test account, shows status, time, body and headers, and the call as curl (with `$NOEGLE` instead of a key). Saving console calls as test cases is not built (MCP test cases cover operations) |
| API-11 | Rate limits per key and operation (`limits.per_key`, token bucket) → 429 + Retry-After. `dev_api_log` (never answers or keys' secrets), minute aggregates like MCP (`api:<slug>`), dashboard per key, alarms to R |
| API-12 | Every answer has an ETag over the data (If-None-Match → 304) and `Cache-Control: private, max-age=<ttl>` (keyed answers must not sit in shared caches; this is stricter than the spec's "private only for delegated"). CORS per API (`cors` origins). Subscribers to "snapshot opdateret" use Gær's webhooks (`snapshot.updated`) |

## Export and import (F-22 … F-24)

- Instance keys: Ed25519 (signing) and X25519 (encryption), created the first time, private keys encrypted with
  `secrets_box`, public keys with fingerprints on `/.well-known/gaer-instance-keys` and in /developer/eksport.
- Export: one JSON file (`gaer-export/1`) with a manifest (type, slug, version, SHA-256 of the definition), the
  definitions with internal ids replaced by references (`{"$ref": "operation/<slug>"}`, `{"$dataset": "<kilde>/<datasæt>"}`),
  catalog entries for the operations' datasets, and an Ed25519 signature over the canonical JSON. Secrets are only
  references; tokens and snapshot data are never in it.
- Import (Udvikler-admin, step-up): first a check — the signer must be a known instance (an admin adds its public key
  and confirms the fingerprint by another channel) or this instance, the signature must match, and every reference must
  exist here or in the file; otherwise the import is blocked and the problems are listed. Then each artifact is
  validated again here and saved as a new version through the approval flow; catalog entries become a catalog version
  with the reason "importeret".
- Not built: exporting secret values encrypted to the receiver's X25519 key (F-24's optional path), and external MCP
  servers through Gær (MCP-8).
