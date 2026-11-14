# Runtime: operations, masking, cache, MCP and API on the same operation

## Operations (fase 3; API-2, DS-9)

An **operation** (`app/devkit/operations.py`, artifact type `operation`) is one query: a catalog dataset as source,
input parameters, fixed filters, output fields, a masking policy, sort, limits and cache settings. An MCP tool made in
Datastudie and (in fase 6) an API operation point to the same operation, so MCP and the API always answer alike.

- Tables, foreign tables (FDW) and snapshots → parametrized SQL (`sql.Identifier` for names, `%s` for values) in a
  READ ONLY transaction with a 5 s `statement_timeout` on the analytics connection. Direct-access connections run the
  same query on their own READ ONLY connection. API sources go through the API source reader (`routes/data.py`).
- A parameter may only filter on a field that is shown unmasked — otherwise a client could probe a masked value.
- `execute()` order: validate arguments → cache → query → **mask** → store (masked) → return. The policy is computed
  at run time from the catalog *now* and the approved version's choices: a field the catalog made stricter since is
  masked harder at once; a policy that has become weaker than allowed stops the call (409) until the operation is
  reviewed.
- The contract is the output fields (type, classification, personal data) and the parameters, so removing a field or
  adding a required parameter is a major and flags the endpoints that use the operation.

## Masking (F-10)

`app/devkit/masking.py` is the only place values are masked. It runs on every row before data is returned (MCP, API,
"Prøv", the guide's samples), before it is cached, and before it is shown in previews. Logs never hold rows: an MCP
call logs its arguments with the values of parameters on non-white fields masked (`mask_args`, MCP-14).

## MCP endpoints from Datastudie (DS-3 … DS-8, MCP-3 … MCP-6)

- The guide: 1 Navn (slug → `/mcp/<slug>`; the endpoint keys of `app/mcp/endpoints.py`), 2 Beskrivelse, 3 Datakilder
  (AI suggestions from the catalog with a relevance when AI is on, a word match otherwise; snapshots first, with their
  age, PL-16), 4 Detaljer (fields, parameters, the tool's level), 5 Eksempler (a live sample, masked, with the
  suggested masking per field and only the allowed choices). "Send til godkendelse" makes one operation per dataset
  and submits the endpoint. Each step saves the draft. DS-4 says "tekst og billeder": the description is text only.
- The endpoint is an artifact of type `mcp_endpoint`; publishing writes the `mcp_endpoints` row (with `artifact_id`)
  and one `mcp_tools` row of the new kind `operation` per tool, so clients only see approved versions. MCP Studio and
  /admin/data/tools cannot change those (409); /admin/data/tools may still switch one off (MCP-3).
- Levels (MCP-5): the tool's classification. White: everyone who may use the endpoint. Green: internal users.
  Yellow: a grant the user approves; red: the user and an approver in /admin — the existing access requests
  (`access_request`). External users do not see non-white operation tools in `tools/list` at all.
- Disabling the artifact disables the endpoint. A major in an operation flags the endpoint "kræver review".

## Cache (F-17 … F-21)

`app/devkit/cache.py`: Redis when `REDIS_URL` is set (a small RESP client, no new dependency), otherwise Postgres
(`dev_cache`). When Redis cannot be reached, each call falls back to Postgres; the app never needs Redis to start.

- Key: artifact, version (or the draft), step, a hash of the parameters, a generation (the artifact's
  `dev_cache_generations` and the dataset's `generation`, so "Ryd cache", a new version and a new snapshot refresh make
  old entries unreachable), and the user for delegated sources (F-19; `clear_user` removes a user's entries).
- Values: AES-256-GCM in the application (`app/secrets_box.py`, the instance key provider), only ever masked rows.
- TTL per operation (`cache.ttl`, seconds; 0 = no cache) and per multisource step; snapshots default to "until the next
  refresh" (the generation changes). Stale-while-revalidate: `cache.swr` seconds past the TTL the value is returned as
  `stale`. The answer says whether it came from the cache and how old it is.
- Eviction: Redis by its own TTL (set `maxmemory-policy allkeys-lru`); Postgres by the job `developer.cache_cleanup`
  (every 10 minutes): expired entries, then the least recently used above `DEV_CACHE_MAX_ENTRIES`.

## Rate limits and idempotency

Token buckets per key (`cache.take(key, per_minute)`): Redis Lua script or one Postgres statement
(`dev_rate_buckets`). Idempotency keys (`idem_get`/`idem_put`): Redis `SET NX PX 24h` or `dev_idempotency`.

## Multisource sources (fase 5; DS-10 … DS-17)

`app/devkit/multisource.py`, artifact type `multisource`, UI `/developer/datastudie/multisource`.

- A definition is a list of steps (JSON) with `operation` (an operation id), `http` (`{source, dataset}` of an API source
  with app access: API key, bearer or OAuth client credentials from Hemmeligheder) or `delegated: true`.
- The graph runs level by level (`levels()`); steps on one level run in parallel (`asyncio.gather`). Roots are merged on
  `key`; `after` + `join` merges by local → foreign; `foreach` enriches each element (one row: fields merged; several:
  a list under the step id), at most `concurrency` calls at a time, or in batches of `batch.size` through an operation
  parameter with op `in` (`WHERE id = ANY(...)`). A foreach on the base step enriches the result.
- Pagination: offset for operations (`paginate: true`), `cursor` or `nextLink` (Microsoft Graph, `@odata.nextLink`)
  for HTTP steps, up to `max_items`. A nextLink to a host outside the source's hosts is refused.
- Limits: `max_items`, one `deadline_ms` for the whole call, `timeout_ms` per call, `retries` with exponential backoff
  and jitter; HTTP 429 waits for `Retry-After` (at most 30 s and never past the deadline).
- `partial: mark` (default): a failing element gets `_fejl: {step: message}` and the answer `partial: true`;
  `partial: fail`: the first failure stops the call (424).
- Every step masks before anything is merged or cached (operations mask; HTTP steps by their dataset's fields).
- Cache per step with its own TTL; `delegated` steps put the user in the key (F-19) and answer "ikke tilgængelig endnu".
- A multisource source can be a tool on a Datastudie endpoint (`{"name": …, "multisource": id}`); its parameters are
  always masked in the MCP log.
- Microsoft Graph with app access is an API source like any other (base_url, `auth: oauth2` client credentials, rows
  `$.value[*]`, `paginate: {type: nextLink}`); `$batch` (up to 20 calls) is not used — batch calls go through operations.
