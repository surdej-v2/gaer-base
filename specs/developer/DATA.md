# Data og Pipeline (fase 2): connections, FDW, snapshots, runs

## Where things live

| What | Where | Setting |
| --- | --- | --- |
| Catalog, artifacts, runs | this database (`public`) | `DATABASE_URL` |
| Snapshots (materialized views) | schema `snapshots` | `ANALYTICS_DATABASE_URL` — default: this database. Set it to a separate `gaer_analytics` database (same server or another) without code changes; create the schema `snapshots` there and give the runtime role `usage` and the pipeline role `usage, create` |
| Foreign tables | schema `fdw_<forbindelse>` in the analytics database | |
| The role pipelines write with | | `PIPELINE_DATABASE_URL` — default the analytics connection |
| The role that creates foreign servers | | `DEV_FDW_ADMIN_URL` — default `MIGRATION_DATABASE_URL` while analytics is this database |
| The load status | `ingest.load_status` | `INGEST_STATUS_TABLE` (`schema.tabel`): "Har indlæseren allerede en statustabel, bruges den i stedet" — it must have `source, load_date, status` |

Migration 0033 creates `snapshots`, `ingest.load_status` (with a trigger that sends `NOTIFY load_status, '<kilde>'`),
and installs `postgres_fdw` when the server has it and the migration role may (otherwise it says so in a notice, and
Forbindelser shows "postgres_fdw mangler"). `mysql_fdw` is never installed by the app: when it is missing, a MySQL
connection says so clearly and does nothing else.

With a separate analytics database, the tables of Gær base itself are not in it: reach them through a `postgres`
connection (FDW) like any other database.

## Connections (F-11, F-12, PL-5)

A connection is a row in `data_sources` (kinds `postgres`, `mysql`, `graph`, `delegated`; `rest` are the API sources
from API Studio, shown under Forbindelser → Live-kilder) plus a `connection` artifact. Reusing `data_sources` means a
connection gets secrets with "Brug i kilde" from Hemmeligheder (migration 0027) without any new rules.

- The definition is JSON: `type, host, port, database, user, password, schema, access (fdw|direct), tables, ingest_source`.
  `password` is a reference (`gruppe/navn[@miljø]` or `DATA_SECRET_…`); a value is refused.
- All secret access goes through `app/devkit/secrets.py` — one interface the Secrets studio can take over.
- `access: fdw`: "Opdatér katalog" creates the server `gaer_conn_<key>`, user mappings for the runtime and pipeline
  roles (and the admin role, which imports the schema), and `IMPORT FOREIGN SCHEMA … LIMIT TO (<tables>) INTO
  fdw_<key>` — only the tables the definition lists. The password is taken from the vault at each sync and kept in the
  user mapping (only superusers can read user-mapping options); rotate = sync again.
- `access: direct` (PostgreSQL): a READ ONLY connection with a 5 s statement timeout per query. Catalog, Udforsk and
  previews work; pipelines need FDW (they join inside PostgreSQL).
- Tables become datasets of kind `foreign`. Columns that look like secrets (`password`, `token`, `hash` …) are left out.
  New fields start yellow (masked) until they are classified.
- `delegated` is the extension point for live sources with the caller's own token (Login-udbydere, built separately).
  It can be created and answers "ikke tilgængelig endnu".
- Only Udvikler-admin (and Fuld) may create, change, test and sync connections (ROLES.md).

## Pipelines (PL-1 … PL-17)

Code: `app/devkit/pipelines.py` (parse, compile, preview, small groups), `app/devkit/pipeline_runs.py` (runs, plan).

- **Definition** (YAML or JSON, `parse`): `name, schedule ("dagligt 06:00" | "hver time" | "manuelt", or
  {type, earliest, latest}), wait_for_load, load_sources, key, max_minutes, min_group_size, paused, history, stages`.
- **Stages** → one CTE each: `source` (FROM a catalog table, foreign table or snapshot), `match` (WHERE; `$eq $ne $gt
  $gte $lt $lte $in $exists $like $or $and`), `lookup` (LEFT JOIN, optional `where` on the other side), `unwind`
  (CROSS JOIN LATERAL unnest/jsonb_array_elements_text), `addFields`/`set` (`$add $subtract $multiply $divide $concat
  $round $lower $upper $coalesce $year $abs`), `project`, `group` (`count, sum(), avg(), min(), max(), count_distinct()`),
  `sort`, `limit`, `unionWith` (UNION ALL), `mask` (masking functions in SELECT). Values are `sql.Literal`, names are
  `sql.Identifier` and must exist in the stage before: a definition cannot inject SQL.
- **Push-down**: the CTEs are referenced once, so PostgreSQL inlines them and postgres_fdw sends filters and the field
  list to the source. A lookup against another source (or a foreign table) without `where` gives the warning
  "… er ikke indsnævret før opslaget".
- **Lineage and classification (PL-14, F-9)**: the compiler knows each output column's source fields and stage. A
  column gets the strictest classification of its sources and their personal-data mark; `count` is white. When the
  snapshot is registered, a field the catalog made stricter keeps that; making it weaker is a catalog major with a
  reason.
- **Preview (PL-4)**: after any stage, `PIPELINE_PREVIEW_ROWS` rows (20) in a READ ONLY transaction with
  `PIPELINE_PREVIEW_TIMEOUT_MS` (5 s), masked by the stage's columns, with the generated SQL.
- **Small groups (PL-15)**: groups with fewer than `min_group_size` rows (5; can be raised, never lowered) give a
  warning; when the output has personal data or red fields, the version cannot be submitted (an artifact gate).
- **Versions (PL-17)**: a run uses the version that is published when it starts. The contract is the output fields
  and the key, so a removed or retyped field is a major and flags what uses the snapshot (PL-16).
- **Materialization (PL-6)**: first run `create materialized view snapshots.<slug>` + unique index on the key; then
  `REFRESH MATERIALIZED VIEW CONCURRENTLY` (readers never block). When the structure changed, a new view is built next
  to the old and swapped in the same transaction. `statement_timeout` = `max_minutes`.
- **History (PL-7)**: `history: {keep_days: N}` also keeps a dated table `snapshots.<slug>__YYYYMMDD` and drops older ones.
- **Runs (PL-9 … PL-11)**: job `pipeline.run` (one at a time per pipeline); phases with times in
  `dev_pipeline_run_phases`. A failure keeps the last good snapshot, sets status Fejlet with the error and tells R.
  "Kør nu" and "Kør igen" start a manual run. Webhooks `snapshot.updated` and `pipeline.run.failed`.
- **Plan (PL-8, PL-12, afsnit 10)**: job `pipeline.schedule` every 5 minutes (the safety net from the spec; the
  NOTIFY from `ingest.load_status` is sent, and a listener can trigger the job sooner, but the app does not depend on
  it). A daily pipeline starts when all its load sources have `succeeded` for today, never before `earliest` (06:00).
  At `latest` (08:00) without the load, the day gets a run "Udskudt" and R is told; when the load arrives later that
  day, the pipeline still runs. A pipeline that reads another's snapshot waits for that one's run today; cycles are
  refused when a version is saved. Load sources: `load_sources` in the definition, or the `ingest_source` of each
  connection the pipeline reads. Times are Europe/Copenhagen.
- **Catalog (PL-13)**: each successful run registers or updates the dataset `snapshots.<slug>` (kind `snapshot`)
  with fields and lineage, `refreshed_at`, and `generation + 1` (cache keys include it, F-21).
- **AI (PL-3)**: "Foreslå pipeline" sends the catalog's names and fields (never rows) to the AI layer; hidden when AI
  is off.
- **Roles**: in production the pipeline role should only read the sources and write `snapshots`; by default it is the
  app role (documented trade-off for a single-database install).
