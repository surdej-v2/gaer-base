# Gær Developer

`/developer` — the developer area of Gær base with four studios (Datastudie, Pipeline, MCP-studie, API-studie) on
one shared core: artifacts with owners and versions, the data catalog, masking, secrets, jobs and one runtime that
MCP and the API both use. The full requirements are the PDF "Gær Developer – samlet specifikation" (requirement ids
F-, DS-, PL-, MCP-, API-, NAV-). This folder records how they were built in this repo and every decision taken on
the way. The repo's own rules win over the spec's technology choices (raw SQL, psycopg, jobs, audit, routes.json).

| File | What |
| --- | --- |
| `README.md` | status per phase, the map of the code, decisions |
| `ROLES.md` | Udvikler and Udvikler-admin in the admin-role model |
| `NAVIGATION.md` | the sidebar, top bar and command palette (NAV-1 … NAV-13) |
| `DATA.md` | connections, FDW, snapshots in `gaer_analytics`/`snapshots`, the ingest status table, pipelines |
| `RUNTIME.md` | operations, masking, cache, rate limits, idempotency, multisource, MCP and API on the same operation |

## Status

| Phase | State | Requirements | Notes |
| --- | --- | --- | --- |
| 1 Fundament | built | F-1 … F-10, DS-1, DS-2, NAV-1 … NAV-13 | |
| 2 Data og Pipeline | built | F-11, F-12, PL-1 … PL-17 | MySQL needs mysql_fdw on the database server |
| 3 Datastudie og MCP | built | DS-3 … DS-9, MCP-3 … MCP-6, F-17 … F-21 (cache) | DS-4: description is text only (no images) |
| 4 MCP-studie | built | MCP-1, MCP-2, MCP-7, MCP-9 … MCP-15 | MCP-8 (external MCP servers) not built |
| 5 Live-kilder | built for app access | DS-10 … DS-17, F-11 (REST/Graph app access) | Delegated access (F-13 … F-16, delegated parts of F-18, F-19, DS-17) is the separate "Login-udbydere" work: extension point only |
| 6 API og flytbarhed | built | API-1 … API-12, F-22 … F-24 | Not built: code operations (PUT/PATCH/DELETE answer 405), saving console calls as test cases, exporting secret values encrypted to the receiver |

### Extension points left for "Login-udbydere" (delegated OAuth)

- Connection type `delegated` (`data_sources.kind`, `app/devkit/connections.py`): can be created, says
  "Delegeret adgang (OAuth pr. bruger) er ikke tilgængelig endnu", and test/sync answer 409.
- Multisource steps with `delegated: true` (`app/devkit/multisource.py`): the cache key already holds the user
  (`cache.make_key(user_id=…)`, F-19), and `cache.clear_user(user_id)` removes a user's entries when access is revoked.
  The token broker plugs in where `_Run.fetch` raises `NOT_YET`.

### Setup

| Setting | Default | What |
| --- | --- | --- |
| `REDIS_URL` | none | Cache, rate limits and idempotency in Redis; without it Postgres. Recommend `maxmemory-policy allkeys-lru` |
| `ANALYTICS_DATABASE_URL` | this database | Where `snapshots` and the `fdw_<forbindelse>` schemas live (gaer_analytics) |
| `PIPELINE_DATABASE_URL` | the analytics connection | The role pipelines write snapshots with |
| `DEV_FDW_ADMIN_URL` | `MIGRATION_DATABASE_URL` | A role that may create foreign servers and import foreign schemas |
| `INGEST_STATUS_TABLE` | `ingest.load_status` | The loader's status table |
| `PIPELINE_PREVIEW_ROWS`, `PIPELINE_PREVIEW_TIMEOUT_MS` | 20, 5000 | Pipeline previews |
| `DEV_CACHE_MAX_ENTRIES` | 20000 | Postgres cache size before the least recently used go |
| Extensions | | `postgres_fdw` (installed by migration 0032 when allowed), `mysql_fdw` (by the database operator) |

## Map of the code

| Path | What |
| --- | --- |
| `server/app/devkit/artifacts.py` | artifacts, versions, RACI, approvals, publish, dependencies, reviews, messages (jobs `developer.mail`, `developer.impact`) |
| `server/app/devkit/contracts.py` | semantic versions and the contract diff that decides patch/minor/major (F-2) |
| `server/app/devkit/masking.py` | the one masking function (F-10) |
| `server/app/devkit/catalog.py` | datasets as versioned artifacts, lineage (F-5, F-7 … F-9) |
| `server/app/devkit/assist.py` | AI suggestions and impact analysis through the instance's AI layer; heuristics without AI |
| `server/app/routes/developer.py` | `/api/admin/dev`: overview, artifacts, approvals, reviews, messages, catalog, developers, search |
| `server/app/migrations/0031_developer_foundation.sql` | artifact tables, catalog columns, lineage, roles |
| `web/src/developer/DeveloperShell.tsx`, `nav.ts`, `developer.css` | the shell: sidebar, top bar, palette |
| `web/src/developer/Overview.tsx`, `Approvals.tsx`, `Catalog.tsx`, `devui.tsx`, `Help.tsx` | phase 1 pages |
| `server/app/devkit/connections.py`, `secrets.py`, `pipelines.py`, `pipeline_runs.py`, `routes/developer_data.py` | phase 2 (DATA.md) |
| `server/app/devkit/operations.py`, `mcp_endpoints.py`, `cache.py`, `mcp/operation_tools.py`, `routes/developer_studio.py` | phase 3 (RUNTIME.md) |
| `server/app/devkit/mcp_studio.py`, `routes/developer_mcp.py` | phase 4 (MCP.md) |
| `server/app/devkit/multisource.py` | phase 5 (RUNTIME.md) |
| `server/app/devkit/apis.py`, `portability.py`, `routes/developer_api.py`, `routes/gaer_api.py` | phase 6 (API.md) |
| `server/app/migrations/0031` … `0035` | the schema, one migration per phase (0034 is MCP-studie, 0035 API and export) |
| `server/tests/test_developer_*.py` | the tests per phase |

## Decisions (phase 1)

- **Names.** The spec's tables are prefixed `dev_` (`dev_artifacts`, `dev_artifact_versions`, `dev_artifact_roles`,
  `dev_artifact_approvals`, `dev_artifact_dependencies`, `dev_reviews`, `dev_notifications`). The catalog is the
  existing `datasets`/`dataset_fields` from 0012 with new columns (`kind`, `personal_data`, `sensitive`, `mask`,
  `generation`, `refreshed_at`) and a new `dataset_lineage` table, not new `catalog_*` tables: /admin/data and
  /developer read the same catalog.
- **API path.** The developer API lives under `/api/admin/dev` so `require_admin` and the admin-role table guard it
  and audit every read, exactly like the rest of /admin. The pages are served by the app on `/` (route group
  `developer`, `chrome: "own"`).
- **Who approves.** Approvers are fixed when a version is submitted: R and C for a minor; A, R and C for a major and
  for the first version. Whoever submits counts as approved in the roles they hold (they made the change). When the
  approval flow is off (default, F-4 opt-in) a version is published when it is submitted; the history is kept either way.
- **Downclassification.** Lowering a field's classification, or removing its personal-data mark, is a major and
  cannot be submitted without a reason (F-9, PL-14).
- **Major → review (F-6).** On submit, owners (A, R) of everything that uses the artifact become C. On publish,
  everything that uses it gets a `dev_reviews` row and status "kræver review"; an owner (A or R) approves it. The AI
  impact analysis is a job (`developer.impact`) that only runs when AI is configured.
- **Informed.** Every published version sends a message to I: a row in `dev_notifications` (shown in Oversigt) and a
  mail through the job `developer.mail`.
- **Masking (F-10).** One function (`masking.mask_row`/`mask_rows`). Defaults: white/green show, yellow interval,
  red mask; a field marked as personal data is treated as at least red. A policy may be stricter, never weaker
  (`policy_for` refuses). Columns the policy does not know are masked (strict). `aggregate` removes the field from
  rows. Hashes are HMAC with a key derived from `SECRET_KEY`, so they are stable per instance and not reversible.
- **Catalog edits (F-5).** A dataset gets a `dataset` artifact the first time it is changed from /developer; version
  1.0.0 is the catalog as it was. /admin/data keeps its direct edits for admins with the role Data (unchanged).
- **AI (F-8).** Suggestions use the existing AI settings (`AI_API_KEY`, `AI_MODEL`, …) and count as AI usage on the
  surface `developer`. Without AI, a heuristic (the most similar classified field by name) answers, and the UI says so.
- **Erasure.** Roles, approvals and messages go with the user (`on delete cascade`); versions keep `created_by = null`.
  An artifact whose only A is erased has no A until an owner or a Udvikler-admin sets one.
