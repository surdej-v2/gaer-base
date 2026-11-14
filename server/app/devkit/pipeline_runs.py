"""Pipeline runs and the plan (PL-6 … PL-13, afsnit 10 "Genforsøg"): jobs `pipeline.run` and `pipeline.schedule`.

A run goes through these phases, each logged with its time (dev_pipeline_run_phases):
  1. wait     — from queued until the worker starts (the plan only queues a run when the load is done)
  2. compile  — the version that is published when the run starts (PL-17)
  3. build    — first time, or when the structure changed: a new materialized view next to the old one, swapped in one
                transaction; otherwise REFRESH MATERIALIZED VIEW CONCURRENTLY (readers are never blocked)
  4. catalog  — the snapshot as a dataset (PL-13) with inherited classification and lineage (PL-14), generation + 1
  5. webhook  — "snapshot.updated"
statement_timeout is the pipeline's max_minutes. A failed run keeps the last good snapshot, and Responsible is told
(PL-10). The plan (`pipeline.schedule`, every 5 minutes) starts a daily pipeline when every source it waits for has
`succeeded` for today in ingest.load_status, never before `earliest` (06:00); at `latest` (08:00) without the load the
day gets a run with status "Udskudt" and Responsible is told; when the load comes later the same day, it still runs.
Pipelines that read another pipeline's snapshot wait for its run today (PL-12).
"""

import json
import re
import time
from datetime import timedelta

import psycopg
import yaml
from psycopg import sql

from app import config, db, jobs, webhooks
from app.devkit import artifacts, connections, pipelines
from app.logs import RequestMeta, audit

RUN_DA = {"queued": "I kø", "waiting": "Venter", "running": "Kører", "succeeded": "OK", "failed": "Fejlet", "deferred": "Udskudt"}
PHASE_DA = {"wait": "Venter på indlæsning", "compile": "Oversætter", "build": "Bygger view", "refresh": "Genopfrisker",
            "index": "Indekserer", "history": "Gemmer historik", "catalog": "Opdaterer kataloget"}


def snapshot_key(slug: str) -> str:
    return f"snapshots.{slug}"


# ── Artifact types ──────────────────────────────────────────────────────────

artifacts.register("pipeline", artifacts.TypeSpec(
    contract=pipelines.contract, dependencies=pipelines.dependencies,
    other={"schedule": "Frekvens", "max_minutes": "Maks. køretid", "paused": "Pause", "description": "Beskrivelse",
           "wait_for_load": "Vent på indlæsning", "stages": "Trin", "min_group_size": "Mindste gruppestørrelse",
           "history": "Historik"}))
artifacts.GATES.setdefault("pipeline", []).append(pipelines.gate)


async def _connection_publish(conn, artifact: dict, v: dict, prev: dict | None) -> None:
    await conn.execute("update data_sources set spec_yaml = %s, updated_at = now() where artifact_id = %s",
                       (json.dumps(v["definition"], ensure_ascii=False), artifact["id"]))


artifacts.register("connection", artifacts.TypeSpec(
    contract=connections.contract, on_publish=_connection_publish,
    other={"host": "Vært", "port": "Port", "database": "Database", "user": "Bruger", "password": "Hemmelighed",
           "schema": "Schema", "access": "Adgang", "ingest_source": "Indlæsningskilde", "sslmode": "SSL"}))


# ── Ingest status ───────────────────────────────────────────────────────────

def _ingest_table() -> sql.Composable:
    name = config.settings.ingest_status_table
    if not re.match(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$", name):
        raise RuntimeError("INGEST_STATUS_TABLE skal være schema.tabel")
    schema, table = name.split(".")
    return sql.Identifier(schema, table)


async def load_status(day) -> dict[str, dict]:
    async with db.analytics_connection() as conn:
        rows = await db.fetch_all(conn, sql.SQL("select * from {} where load_date = %s").format(_ingest_table()), (day,))
    return {r["source"]: r for r in rows}


async def load_sources(conn, definition: dict) -> list[str]:
    """What a pipeline waits for: its own list, or the ingest source of every connection it reads."""
    if not definition.get("wait_for_load"):
        return []
    if definition.get("load_sources"):
        return list(definition["load_sources"])
    rows = await db.fetch_all(conn, """select distinct s.key, s.spec_yaml from datasets d join data_sources s on s.id = d.source_id
                                       where d.id = any(%s) and s.kind in ('postgres', 'mysql')""", (definition.get("_datasets") or [],))
    out = []
    for r in rows:
        try:
            spec = yaml.safe_load(r["spec_yaml"]) or {}
        except yaml.YAMLError:
            spec = {}
        out.append(spec.get("ingest_source") or r["key"])
    return sorted(set(out))


# ── Starting a run ──────────────────────────────────────────────────────────

async def start(conn, artifact: dict, *, trigger: str, requested_by: int | None = None, status: str = "queued",
                error: str | None = None) -> int:
    if not artifact["current_version_id"]:
        raise pipelines.PipelineError("Pipelinen har ingen udgivet version endnu")
    busy = await db.fetch_one(conn, """select id from dev_pipeline_runs where artifact_id = %s and status in ('queued', 'running', 'waiting')""",
                              (artifact["id"],))
    if busy and status == "queued":
        raise pipelines.PipelineError("Pipelinen kører allerede")
    row = await db.fetch_one(conn, """insert into dev_pipeline_runs (artifact_id, version_id, run_date, trigger, status, requested_by, error,
                                      finished_at) values (%s, %s, %s, %s, %s, %s, %s, case when %s = 'deferred' then now() end) returning id""",
                             (artifact["id"], artifact["current_version_id"], pipelines.now_local().date(), trigger, status,
                              requested_by, error, status))
    if status == "queued":
        await jobs.enqueue(conn, "pipeline.run", {"run_id": row["id"]}, max_attempts=30, dedupe_key=f"pipeline.run:{artifact['id']}")
    return row["id"]


# ── The run ─────────────────────────────────────────────────────────────────

class _Phases:
    def __init__(self, run_id: int):
        self.run_id = run_id
        self.items: list[tuple] = []

    def add(self, phase: str, started: float, wall, detail: str = "") -> None:
        self.items.append((phase, PHASE_DA.get(phase, phase), wall, int((time.monotonic() - started) * 1000), detail))

    async def save(self) -> None:
        async with db.connection() as conn:
            for phase, label, wall, ms, detail in self.items:
                await conn.execute("""insert into dev_pipeline_run_phases (run_id, phase, label, started_at, ms, detail)
                                      values (%s, %s, %s, %s, %s, %s)""", (self.run_id, phase, label, wall, ms, detail[:500]))


async def _mv_columns(conn, slug: str) -> list[tuple[str, str]] | None:
    rows = await db.fetch_all(conn, """select a.attname, format_type(a.atttypid, a.atttypmod) as t from pg_attribute a
                                       join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
                                       where n.nspname = 'snapshots' and c.relname = %s and c.relkind = 'm' and a.attnum > 0
                                       and not a.attisdropped order by a.attnum""", (slug,))
    return [(r["attname"], r["t"]) for r in rows] if rows else None


@jobs.handler("pipeline.run")
async def run_job(payload: dict, job: dict) -> dict:
    async with db.connection() as conn:
        run = await db.fetch_one(conn, "select * from dev_pipeline_runs where id = %s for update", (payload["run_id"],))
        if run is None or run["status"] not in ("queued", "waiting"):
            return {"skipped": True}
        artifact = await artifacts.get(conn, run["artifact_id"])
        v = await artifacts.current(conn, artifact)
        # PL-12: wait while a pipeline this one reads is running.
        deps = await db.fetch_all(conn, """select r.id from dev_pipeline_runs r join datasets d on d.pipeline_artifact_id = r.artifact_id
                                           where d.id = any(%s) and r.status in ('queued', 'running', 'waiting') and r.id <> %s""",
                                  (v["definition"].get("_datasets") or [], run["id"]))
        if deps:
            await conn.execute("update dev_pipeline_runs set status = 'waiting' where id = %s", (run["id"],))
            await conn.commit()
            raise jobs.Retry("venter på en pipeline, den afhænger af")
        await conn.execute("update dev_pipeline_runs set status = 'running', started_at = now(), version_id = %s where id = %s",
                           (v["id"], run["id"]))
        started_row = await db.fetch_one(conn, "select started_at, queued_at from dev_pipeline_runs where id = %s", (run["id"],))
    phases = _Phases(run["id"])
    phases.items.append(("wait", PHASE_DA["wait"], started_row["queued_at"],
                         int((started_row["started_at"] - started_row["queued_at"]).total_seconds() * 1000), ""))
    definition = v["definition"]
    slug = artifact["slug"].replace("-", "_")
    try:
        t, wall = time.monotonic(), pipelines.now_local()
        async with db.connection() as conn:
            compiled = await pipelines.compile_definition(conn, definition)
        phases.add("compile", t, wall, f"{len(compiled.ctes)} trin")
        query = compiled.query()
        key = compiled.key
        async with db.pipeline_connection() as pc:
            await pc.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(f"{definition.get('max_minutes', 15)}min")))
            existing = await _mv_columns(pc, slug)
            t, wall = time.monotonic(), pipelines.now_local()
            if existing is None:
                await pc.execute(sql.SQL("create materialized view {} as {} with data").format(sql.Identifier("snapshots", slug), query))
                if key:
                    await pc.execute(sql.SQL("create unique index {} on {} ({})").format(
                        sql.Identifier(f"{slug}_key"), sql.Identifier("snapshots", slug), sql.Identifier(key)))
                phases.add("build", t, wall, "nyt snapshot")
            else:
                new = f"{slug}__new"
                await pc.execute(sql.SQL("drop materialized view if exists {}").format(sql.Identifier("snapshots", new)))
                await pc.execute(sql.SQL("create materialized view {} as {} with no data").format(sql.Identifier("snapshots", new), query))
                same = await _mv_columns(pc, new) == existing
                await pc.execute(sql.SQL("drop materialized view {}").format(sql.Identifier("snapshots", new)))
                has_index = await db.fetch_one(pc, """select 1 from pg_indexes where schemaname = 'snapshots' and tablename = %s
                                                     and indexname = %s""", (slug, f"{slug}_key"))
                if same and key and has_index:
                    await pc.execute(sql.SQL("refresh materialized view concurrently {}").format(sql.Identifier("snapshots", slug)))
                    phases.add("refresh", t, wall, "REFRESH CONCURRENTLY")
                elif same and not key:
                    await pc.execute(sql.SQL("refresh materialized view {}").format(sql.Identifier("snapshots", slug)))
                    phases.add("refresh", t, wall, "REFRESH")
                else:
                    # A new structure (a major): build next to the old one, swap in this transaction.
                    await pc.execute(sql.SQL("create materialized view {} as {} with data").format(sql.Identifier("snapshots", new), query))
                    if key:
                        await pc.execute(sql.SQL("create unique index {} on {} ({})").format(
                            sql.Identifier(f"{new}_key"), sql.Identifier("snapshots", new), sql.Identifier(key)))
                    await pc.execute(sql.SQL("drop materialized view {}").format(sql.Identifier("snapshots", slug)))
                    await pc.execute(sql.SQL("alter materialized view {} rename to {}").format(sql.Identifier("snapshots", new), sql.Identifier(slug)))
                    if key:
                        await pc.execute(sql.SQL("alter index {} rename to {}").format(sql.Identifier("snapshots", f"{new}_key"), sql.Identifier(f"{slug}_key")))
                    phases.add("build", t, wall, "ny struktur: byttet i én transaktion")
            stats = await db.fetch_one(pc, sql.SQL("select count(*) as n, pg_total_relation_size({}) as bytes from {}").format(
                sql.Literal(f"snapshots.{slug}"), sql.Identifier("snapshots", slug)))
            if definition.get("history"):
                t, wall = time.monotonic(), pipelines.now_local()
                day = pipelines.now_local().strftime("%Y%m%d")
                dated = f"{slug}__{day}"
                await pc.execute(sql.SQL("drop table if exists {}").format(sql.Identifier("snapshots", dated)))
                await pc.execute(sql.SQL("create table {} as select * from {}").format(sql.Identifier("snapshots", dated), sql.Identifier("snapshots", slug)))
                keep = definition["history"]["keep_days"]
                cutoff = (pipelines.now_local().date() - timedelta(days=keep)).strftime("%Y%m%d")
                for r in await db.fetch_all(pc, """select tablename from pg_tables where schemaname = 'snapshots' and tablename ~ %s""",
                                            (f"^{re.escape(slug)}__[0-9]{{8}}$",)):
                    if r["tablename"][-8:] < cutoff:
                        await pc.execute(sql.SQL("drop table {}").format(sql.Identifier("snapshots", r["tablename"])))
                phases.add("history", t, wall, f"{dated}, beholdes {keep} dage")
        small = await pipelines.small_groups(compiled, definition.get("min_group_size", pipelines.MIN_GROUP),
                                             timeout_ms=definition.get("max_minutes", 15) * 60_000) if compiled.groups else 0
        t, wall = time.monotonic(), pipelines.now_local()
        async with db.connection() as conn:
            dataset_id = await register_snapshot(conn, artifact, compiled, slug)
            await conn.execute("""update dev_pipeline_runs set status = 'succeeded', finished_at = now(), rows = %s, size_bytes = %s,
                                  small_groups = %s where id = %s""", (stats["n"], stats["bytes"], small, run["id"]))
            await webhooks.emit(conn, "snapshot.updated", {"pipeline_id": artifact["id"], "dataset_id": dataset_id,
                                                           "run_id": run["id"], "rows": stats["n"]})
            await audit(conn, "dev.snapshot.refreshed", actor_id=run["requested_by"], meta=RequestMeta(), table="dev_artifacts",
                        row_id=artifact["id"], run_id=run["id"], rows=stats["n"], version=artifacts.version_str(v))
        phases.add("catalog", t, wall, f"{stats['n']} rækker")
        await phases.save()
        return {"rows": stats["n"], "bytes": stats["bytes"], "small_groups": small}
    except Exception as e:  # noqa: BLE001 — every failure is recorded on the run; the last good snapshot stays
        text = _error_text(e)
        async with db.connection() as conn:
            await conn.execute("update dev_pipeline_runs set status = 'failed', finished_at = now(), error = %s where id = %s",
                               (text, run["id"]))
            await webhooks.emit(conn, "pipeline.run.failed", {"pipeline_id": artifact["id"], "run_id": run["id"]})
            for uid, rs in (await artifacts.roles(conn, artifact["id"])).items():
                if "R" in rs:
                    await artifacts.notify(conn, uid, "pipeline_failed", f"{artifact['name']}: kørslen fejlede",
                                           f"{text} Det sidste gode snapshot bevares.", link=f"/developer/pipeline/{artifact['id']}",
                                           artifact_id=artifact["id"])
        await phases.save()
        return {"failed": text}


def _error_text(e: Exception) -> str:
    if isinstance(e, psycopg.errors.QueryCanceled):
        return "Timeout: kørslen tog længere end den maksimale køretid."
    if isinstance(e, pipelines.PipelineError):
        return str(e.detail)
    if isinstance(e, psycopg.Error):
        return "Databasefejl: " + (str(e).splitlines()[0][:300] if str(e) else type(e).__name__)
    return f"{type(e).__name__}: {str(e)[:300]}"


async def register_snapshot(conn, artifact: dict, compiled: pipelines.Compiled, slug: str) -> int:
    """PL-13/PL-14: the snapshot as a dataset with fields that inherit classification and personal data, and lineage.
    A field the catalog has made stricter keeps that; a weaker one is only possible as a major with a reason."""
    key = snapshot_key(slug)
    d = await db.fetch_one(conn, "select id from datasets where source_id is null and key = %s", (key,))
    if d is None:
        d = await db.fetch_one(conn, """insert into datasets (key, name, description, kind, pipeline_artifact_id)
                                        values (%s, %s, %s, 'snapshot', %s) returning id""",
                               (key, artifact["name"], artifact["description"], artifact["id"]))
    old = {f["name"]: f for f in await db.fetch_all(conn, "select * from dataset_fields where dataset_id = %s", (d["id"],))}
    names = [c.name for c in compiled.columns]
    await conn.execute("delete from dataset_fields where dataset_id = %s and not (name = any(%s))", (d["id"], names))
    for pos, c in enumerate(compiled.columns):
        f = old.get(c.name)
        if f is None:
            f = await db.fetch_one(conn, """insert into dataset_fields (dataset_id, name, data_type, classification, personal_data,
                                            inherited, position, mask) values (%s, %s, %s, %s, %s, true, %s, %s) returning *""",
                                   (d["id"], c.name, c.type, c.classification, c.personal, pos, c.mask))
        else:
            cls = pipelines.masking.strictest([c.classification, f["classification"]])
            await conn.execute("""update dataset_fields set data_type = %s, classification = %s, personal_data = %s, position = %s,
                                  inherited = true where id = %s""", (c.type, cls, c.personal or f["personal_data"], pos, f["id"]))
        await conn.execute("delete from dataset_lineage where field_id = %s", (f["id"],))
        for fid, label in c.sources[:20]:
            await conn.execute("""insert into dataset_lineage (field_id, source_field_id, source_label, step, transform)
                                  values (%s, %s, %s, %s, %s)""", (f["id"], fid, label, c.transform.split("(")[0] or "kilde", c.transform))
    personal = any(c.personal for c in compiled.columns)
    await conn.execute("""update datasets set refreshed_at = now(), generation = generation + 1, updated_at = now(),
                          personal_data = %s, name = %s, pipeline_artifact_id = %s, kind = 'snapshot' where id = %s""",
                       (personal, artifact["name"], artifact["id"], d["id"]))
    return d["id"]


# ── The plan ────────────────────────────────────────────────────────────────

@jobs.handler("pipeline.schedule")
async def schedule_job(payload: dict, job: dict) -> dict:
    now = pipelines.now_local()
    today = now.date()
    started, deferred = [], []
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""select {artifacts.COLUMNS} from dev_artifacts a
                                             where a.type = 'pipeline' and a.status <> 'disabled' and a.current_version_id is not null""")
        for a in items:
            v = await artifacts.current(conn, a)
            d = v["definition"]
            s = d.get("schedule") or {}
            if d.get("paused") or s.get("type") in (None, "manual"):
                continue
            runs = await db.fetch_all(conn, """select status, queued_at from dev_pipeline_runs where artifact_id = %s
                                               and queued_at > now() - interval '2 days' order by id desc""", (a["id"],))
            if any(r["status"] in ("queued", "running", "waiting") for r in runs):
                continue
            if s["type"] == "hourly":
                if not runs or runs[0]["queued_at"] < now - timedelta(hours=1):
                    started.append(await start(conn, a, trigger="schedule"))
                continue
            todays = [r for r in runs if r["queued_at"].astimezone(pipelines.TZ).date() == today]
            if any(r["status"] == "succeeded" for r in todays) or now < pipelines.at(today, s.get("earliest", "06:00")):
                continue
            # PL-12: a pipeline this one reads must have run today first.
            dep_ok = True
            for dep in await db.fetch_all(conn, "select distinct pipeline_artifact_id from datasets where id = any(%s) and pipeline_artifact_id is not null",
                                          (d.get("_datasets") or [],)):
                ok = await db.fetch_one(conn, """select 1 from dev_pipeline_runs where artifact_id = %s and status = 'succeeded'
                                                 and run_date = %s""", (dep["pipeline_artifact_id"], today))
                dep_ok = dep_ok and ok is not None
            if not dep_ok:
                continue
            wanted = await load_sources(conn, d)
            if wanted:
                status = await load_status(today)
                done = all(status.get(w, {}).get("status") == "succeeded" for w in wanted)
                if not done:
                    if now >= pipelines.at(today, s.get("latest", "08:00")) and not any(r["status"] == "deferred" for r in todays):
                        missing = [w for w in wanted if status.get(w, {}).get("status") != "succeeded"]
                        rid = await start(conn, a, trigger="schedule", status="deferred",
                                          error=f"Indlæsningen var ikke færdig kl. {s.get('latest', '08:00')}: {', '.join(missing)}. "
                                                "Det sidste gode snapshot bevares.")
                        deferred.append(rid)
                        for uid, rs in (await artifacts.roles(conn, a["id"])).items():
                            if "R" in rs:
                                await artifacts.notify(conn, uid, "pipeline_deferred", f"{a['name']}: kørslen er udskudt",
                                                       f"Indlæsningen af {', '.join(missing)} er ikke færdig. Kørslen starter, "
                                                       "når den bliver det i dag.", link=f"/developer/pipeline/{a['id']}", artifact_id=a["id"])
                    continue
            started.append(await start(conn, a, trigger="schedule"))
    return {"started": started, "deferred": deferred}
