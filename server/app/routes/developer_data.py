"""/api/admin/dev: connections (Forbindelser) and Pipeline — phase 2 of Gær Developer (specs/developer/DATA.md).

Connections: creating and changing them is for Udvikler-admin (admin_roles.DEV_ADMIN_WRITES); reading for every
developer. Pipelines: every developer makes drafts; publishing follows the artifact's approval flow. Every change is
audited in the same transaction.
"""

import json
import re

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.devkit import artifacts, assist, connections, pipeline_runs, pipelines
from app.devkit import secrets as dev_secrets
from app.logs import audit
from app.sessions import Principal, require_admin

router = APIRouter(prefix="/api/admin/dev", tags=["developer"], dependencies=[Depends(require_admin)])

KEY = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")


def _definition(row: dict) -> dict:
    try:
        return yaml.safe_load(row["spec_yaml"]) or {}
    except yaml.YAMLError:
        return {}


# ── Connections ─────────────────────────────────────────────────────────────

@router.get("/connections/capabilities")
async def capabilities():
    return await connections.capabilities()


@router.get("/connections")
async def list_connections():
    caps = await connections.capabilities()
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select s.id, s.key, s.name, s.kind, s.spec_yaml, s.updated_at, s.artifact_id, a.status as artifact_status,
                   (select count(*) from datasets d where d.source_id = s.id) as datasets
            from data_sources s left join dev_artifacts a on a.id = s.artifact_id order by s.kind, s.name""")
        out = []
        for r in rows:
            d = _definition(r) if r["kind"] != "rest" else {}
            status = await dev_secrets.status(conn, key=r["key"], source_id=r["id"], refs=connections.refs(d)) if d.get("password") else []
            problem = None
            if r["kind"] == "delegated":
                problem = connections.NOT_YET
            elif r["kind"] in ("postgres", "mysql") and d.get("access", "fdw") == "fdw":
                ext = connections.FDW[r["kind"]]
                if not caps[ext]:
                    problem = f"{ext} er ikke installeret"
            if not problem and any(not s["ok"] for s in status):
                problem = next(s["text"] for s in status if not s["ok"])
            out.append({"id": r["id"], "key": r["key"], "name": r["name"], "kind": r["kind"], "kindLabel": connections.TYPES.get(r["kind"], r["kind"]),
                        "access": {"fdw": "FDW", "direct": "Direkte"}.get(d.get("access"), "Live" if r["kind"] in ("rest", "graph") else "Delegeret" if r["kind"] == "delegated" else "—"),
                        "secret": "Hemmelighed i lager" if d.get("password") else ("Kildebeskrivelse" if r["kind"] == "rest" else "Ingen"),
                        "datasets": r["datasets"], "artifactId": r["artifact_id"], "status": "error" if problem else "ok",
                        "problem": problem, "updatedAt": r["updated_at"]})
    return {"items": out, "capabilities": caps}


class ConnectionIn(BaseModel):
    key: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    definition: dict
    approval_flow: bool = False


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionIn, p: Principal = Depends(require_admin)):
    key = body.key.strip().lower()
    if not KEY.match(key):
        raise HTTPException(422, "Nøglen må kun have små bogstaver, tal, - og _ og skal starte med et bogstav")
    d = connections.parse(body.definition)
    async with db.connection() as conn:
        if await db.fetch_one(conn, "select 1 from data_sources where key = %s", (key,)):
            raise HTTPException(409, f"Der findes allerede en kilde med nøglen {key}")
        row = await db.fetch_one(conn, """insert into data_sources (key, name, kind, spec_yaml, hosts) values (%s, %s, %s, %s, %s)
                                          returning id""", (key, body.name.strip(), d["type"], json.dumps(d, ensure_ascii=False),
                                                            [d["host"]] if d.get("host") else []))
        a = await artifacts.create(conn, type_="connection", slug=key, name=body.name, definition=d, actor_id=p.user_id,
                                   meta=p.meta, approval_flow=body.approval_flow)
        await conn.execute("update data_sources set artifact_id = %s where id = %s", (a["id"], row["id"]))
        await dev_secrets.record_use(conn, row["id"], key, connections.refs(d))
        await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="data_sources", row_id=row["id"],
                    after={"key": key, "kind": d["type"], "access": d.get("access")})
    return await connection_detail(row["id"])


async def _source(conn, source_id: int) -> dict:
    s = await db.fetch_one(conn, "select * from data_sources where id = %s", (source_id,))
    if s is None or s["kind"] == "rest":
        raise HTTPException(404, "Forbindelsen findes ikke (API-kilder ligger under Live-kilder)")
    return s


@router.get("/connections/{source_id}")
async def connection_detail(source_id: int):
    async with db.connection() as conn:
        s = await _source(conn, source_id)
        d = _definition(s)
        status = await dev_secrets.status(conn, key=s["key"], source_id=s["id"], refs=connections.refs(d)) if d.get("password") else []
        sets = await db.fetch_all(conn, """select id, key, name, kind, refreshed_at, (select count(*) from dataset_fields f where f.dataset_id = d.id) as fields
                                           from datasets d where source_id = %s order by key""", (source_id,))
        art = await artifacts.detail(conn, s["artifact_id"]) if s["artifact_id"] else None
    problem = connections.NOT_YET if s["kind"] == "delegated" else (
        await connections.fdw_problem(s["kind"]) if d.get("access") == "fdw" else None)
    return {"id": s["id"], "key": s["key"], "name": s["name"], "kind": s["kind"], "kindLabel": connections.TYPES.get(s["kind"]),
            "definition": d, "secrets": status, "datasets": sets, "artifact": art, "problem": problem,
            "schema": connections.schema_name(s["key"]) if d.get("access") == "fdw" else None}


class ConnectionEdit(BaseModel):
    definition: dict
    note: str = Field(default="", max_length=2000)


@router.put("/connections/{source_id}")
async def update_connection(source_id: int, body: ConnectionEdit, p: Principal = Depends(require_admin)):
    d = connections.parse(body.definition)
    async with db.connection() as conn:
        s = await _source(conn, source_id)
        if d["type"] != s["kind"]:
            raise HTTPException(422, "Typen kan ikke ændres. Opret en ny forbindelse")
        a = await artifacts.get(conn, s["artifact_id"], lock=True)
        await artifacts.save_draft(conn, a, d, actor_id=p.user_id, meta=p.meta, note=body.note)
        v = await artifacts.submit(conn, a, actor_id=p.user_id, meta=p.meta, note=body.note)
        await conn.execute("update data_sources set hosts = %s, updated_at = now() where id = %s",
                           ([d["host"]] if d.get("host") else [], source_id))
        await dev_secrets.record_use(conn, source_id, s["key"], connections.refs(d))
    return {"version": artifacts.version_out(v)}


async def _user_row(conn, user_id: int) -> dict:
    return await db.fetch_one(conn, "select * from users where id = %s", (user_id,))


@router.post("/connections/{source_id}/test")
async def test_connection(source_id: int, p: Principal = Depends(require_admin)):
    """Connects and lists the tables in the schema. Uses the source's grant, or the tester's own access to the secret."""
    async with db.connection() as conn:
        s = await _source(conn, source_id)
        d = _definition(s)
        if s["kind"] == "delegated":
            raise HTTPException(409, connections.NOT_YET)
        password = await connections.password_for(conn, s, d, tester=await _user_row(conn, p.user_id), meta=p.meta)
    if s["kind"] == "mysql":
        problem = await connections.fdw_problem("mysql")
        if problem:
            raise HTTPException(409, problem)
        tables = await connections.apply_fdw(s, d, password)
        return {"ok": True, "tables": {t: [] for t in tables}, "via": "fdw"}
    tables = await connections.describe_direct(d, password)
    return {"ok": True, "tables": {k: v for k, v in list(tables.items())[:200]}, "via": "direct"}


@router.post("/connections/{source_id}/sync")
async def sync_connection(source_id: int, p: Principal = Depends(require_admin)):
    """Sets up the foreign tables (FDW) or reads the tables directly, and puts the chosen tables in the catalog."""
    async with db.connection() as conn:
        s = await _source(conn, source_id)
        d = _definition(s)
        if s["kind"] == "delegated":
            raise HTTPException(409, connections.NOT_YET)
        password = await connections.password_for(conn, s, d, tester=await _user_row(conn, p.user_id), meta=p.meta)
    if d.get("access") == "fdw":
        await connections.apply_fdw(s, d, password)
        columns = await connections.describe_fdw(s["key"])
    else:
        columns = await connections.describe_direct(d, password)
    async with db.connection() as conn:
        ids = await connections.import_catalog(conn, s, d, columns)
        await audit(conn, "dev.connection.synced", actor_id=p.user_id, meta=p.meta, table="data_sources", row_id=source_id,
                    tables=d.get("tables"), access=d.get("access"))
    missing = [t for t in d.get("tables") or [] if t not in columns]
    return {"datasets": ids, "missing": missing}


@router.delete("/connections/{source_id}")
async def delete_connection(source_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        s = await _source(conn, source_id)
        used = await db.fetch_one(conn, "select count(*) as n from dev_artifact_dependencies where depends_on = %s", (s["artifact_id"] or 0,))
        if used["n"]:
            raise HTTPException(409, "Forbindelsen bruges af andre artefakter. Fjern dem først")
        await conn.execute("delete from data_sources where id = %s", (source_id,))
        if s["artifact_id"]:
            await conn.execute("delete from dev_artifacts where id = %s", (s["artifact_id"],))
        await audit(conn, "delete", actor_id=p.user_id, meta=p.meta, table="data_sources", row_id=source_id,
                    before={"key": s["key"], "kind": s["kind"]})
    if _definition(s).get("access") == "fdw":
        await connections.drop_fdw(s["key"])
    return {"ok": True}


@router.get("/ingest")
async def ingest(date: str | None = None):
    from datetime import date as d_
    day = d_.fromisoformat(date) if date else pipelines.now_local().date()
    try:
        rows = list((await pipeline_runs.load_status(day)).values())
    except Exception:  # noqa: BLE001 — the status table may live elsewhere or not exist yet
        rows = []
    return {"date": day, "items": rows}


# ── Pipelines ───────────────────────────────────────────────────────────────

def _parse(raw) -> dict:
    return pipelines.parse(raw)


async def _runs_summary(conn, artifact_ids: list[int]) -> dict[int, dict]:
    rows = await db.fetch_all(conn, """select distinct on (artifact_id) artifact_id, id, status, started_at, finished_at, queued_at, rows, error
                                       from dev_pipeline_runs where artifact_id = any(%s) order by artifact_id, id desc""", (artifact_ids,))
    return {r["artifact_id"]: r for r in rows}


@router.get("/pipelines")
async def list_pipelines():
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""select {artifacts.COLUMNS} from dev_artifacts a where a.type = 'pipeline' order by a.name""")
        last = await _runs_summary(conn, [a["id"] for a in items])
        out = []
        for a in items:
            cur = await artifacts.current(conn, a)
            draft = await artifacts.open_version(conn, a["id"])
            r = last.get(a["id"])
            nxt = pipelines.next_run(cur["definition"], r["queued_at"] if r and r["status"] == "succeeded" else None) if cur else None
            out.append({**artifacts.out(a, cur), "lastRun": r, "lastRunLabel": pipeline_runs.RUN_DA.get(r["status"]) if r else None,
                        "next": nxt, "schedule": (cur or draft or {}).get("definition", {}).get("schedule") if (cur or draft) else None,
                        "draft": draft["status"] if draft else None})
    return {"items": out}


class PipelineIn(BaseModel):
    slug: str = Field(min_length=1, max_length=56)
    name: str = Field(min_length=1, max_length=120)
    definition: dict | str
    approval_flow: bool = False


@router.post("/pipelines", status_code=201)
async def create_pipeline(body: PipelineIn, p: Principal = Depends(require_admin)):
    slug = body.slug.strip().lower()
    if not pipelines.SLUG.match(slug):
        raise HTTPException(422, "Navnet må kun have små bogstaver, tal og _ og skal starte med et bogstav")
    d = _parse(body.definition)
    d["name"] = d["name"] or slug
    async with db.connection() as conn:
        prepared, _ = await pipelines.prepare(conn, d)
        a = await artifacts.create(conn, type_="pipeline", slug=slug, name=body.name, description=d.get("description", ""),
                                   definition=prepared, actor_id=p.user_id, meta=p.meta, approval_flow=body.approval_flow,
                                   publish=False)
        return {"id": a["id"]}


@router.get("/pipelines/{artifact_id}")
async def pipeline_detail(artifact_id: int):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        if a["type"] != "pipeline":
            raise HTTPException(404, "Pipelinen findes ikke")
        detail = await artifacts.detail(conn, artifact_id)
        working = detail["draft"] or detail["current"]
        compiled_info, error = None, None
        try:
            compiled = await pipelines.compile_definition(conn, working["definition"])
            compiled_info = {"columns": [c.out() for c in compiled.columns], "warnings": compiled.warnings,
                             "stages": [{"index": i, "kind": next(iter(s)), "label": pipelines.STAGE_DA[next(iter(s))]}
                                        for i, s in enumerate(working["definition"]["stages"])]}
        except HTTPException as e:
            error = e.detail
        runs = await db.fetch_all(conn, """select id, status, trigger, run_date, queued_at, started_at, finished_at, rows, size_bytes,
                                           small_groups, error from dev_pipeline_runs where artifact_id = %s order by id desc limit 30""",
                                  (artifact_id,))
        snap = await db.fetch_one(conn, """select id, key, refreshed_at, generation from datasets where pipeline_artifact_id = %s
                                           and kind = 'snapshot'""", (artifact_id,))
        load = await pipeline_runs.load_sources(conn, working["definition"])
        deps = await db.fetch_all(conn, """select distinct a.id, a.name, a.slug from datasets d join dev_artifacts a on a.id = d.pipeline_artifact_id
                                           where d.id = any(%s)""", (working["definition"].get("_datasets") or [],))
        dependents = await db.fetch_all(conn, """select distinct a.id, a.name from dev_artifact_dependencies dep
                                                 join dev_artifacts a on a.current_version_id = dep.version_id
                                                 where dep.depends_on = %s and a.type = 'pipeline'""", (artifact_id,))
    last_ok = next((r for r in runs if r["status"] == "succeeded"), None)
    return {"artifact": detail, "yaml": pipelines.to_yaml(working["definition"]), "definition": working["definition"],
            "compiled": compiled_info, "error": error, "runs": [{**r, "label": pipeline_runs.RUN_DA[r["status"]]} for r in runs],
            "snapshot": snap, "loadSources": load, "dependsOn": deps, "dependents": dependents,
            "next": pipelines.next_run(detail["current"]["definition"], last_ok["queued_at"] if last_ok else None) if detail["current"] else None}


class PipelineEdit(BaseModel):
    definition: dict | str
    note: str = Field(default="", max_length=2000)


@router.put("/pipelines/{artifact_id}")
async def save_pipeline(artifact_id: int, body: PipelineEdit, p: Principal = Depends(require_admin)):
    d = _parse(body.definition)
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        if a["type"] != "pipeline":
            raise HTTPException(404, "Pipelinen findes ikke")
        prepared, _ = await pipelines.prepare(conn, d)
        await pipelines.check_cycles(conn, artifact_id, prepared)
        v = await artifacts.save_draft(conn, a, prepared, actor_id=p.user_id, meta=p.meta, note=body.note)
    return {"version": artifacts.version_out(v), "downclassified": v["downclassified"]}


class PreviewIn(BaseModel):
    definition: dict | str
    stage: int | None = None


@router.post("/pipelines/preview")
async def preview(body: PreviewIn):
    d = _parse(body.definition)
    async with db.connection() as conn:
        compiled = await pipelines.compile_definition(conn, d)
    result = await pipelines.preview(compiled, body.stage)
    small = 0
    if compiled.groups and (body.stage is None or any(g[0] <= result["stage"] for g in compiled.groups)):
        small = await pipelines.small_groups(compiled, d["min_group_size"], timeout_ms=5000)
    return {**result, "warnings": compiled.warnings, "smallGroups": small, "minGroupSize": d["min_group_size"],
            "sensitive": pipelines.sensitive_output(compiled), "output": [c.out() for c in compiled.columns]}


class FormatIn(BaseModel):
    yaml: str | None = Field(default=None, max_length=64_000)
    definition: dict | None = None


@router.post("/pipelines/format")
async def format_pipeline(body: FormatIn):
    """The visual editor and the text view stay in sync (PL-3): YAML ↔ the normalized definition."""
    d = _parse(body.yaml if body.yaml is not None else body.definition or {})
    return {"definition": d, "yaml": pipelines.to_yaml(d)}


class SuggestIn(BaseModel):
    description: str = Field(min_length=5, max_length=2000)


@router.post("/pipelines/suggest")
async def suggest(body: SuggestIn, p: Principal = Depends(require_admin)):
    if not assist.enabled():
        raise HTTPException(409, "AI er slået fra på denne instans")
    async with db.connection() as conn:
        sets = await db.fetch_all(conn, """select d.id, d.key, d.name, d.description,
                                           array(select f.name from dataset_fields f where f.dataset_id = d.id order by f.position) as field_names
                                           from datasets d where d.kind in ('table', 'foreign', 'snapshot')""")
    stages = await assist.suggest_pipeline(body.description, sets, p.user_id)
    if not stages:
        raise HTTPException(424, "AI kunne ikke foreslå en pipeline. Prøv at beskrive den anderledes")
    return {"stages": stages}


@router.post("/pipelines/{artifact_id}/run")
async def run_now(artifact_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        if a["type"] != "pipeline":
            raise HTTPException(404, "Pipelinen findes ikke")
        run_id = await pipeline_runs.start(conn, a, trigger="manual", requested_by=p.user_id)
        await audit(conn, "dev.pipeline.run", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=artifact_id, run_id=run_id)
    return {"runId": run_id}


@router.get("/runs")
async def runs(artifact_id: int | None = None, limit: int = 100):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select r.id, r.artifact_id, a.name, a.slug, r.status, r.trigger, r.run_date, r.queued_at, r.started_at,
                                           r.finished_at, r.rows, r.size_bytes, r.small_groups, r.error
                                           from dev_pipeline_runs r join dev_artifacts a on a.id = r.artifact_id
                                           where %s::bigint is null or r.artifact_id = %s order by r.id desc limit %s""",
                                  (artifact_id, artifact_id, max(1, min(limit, 500))))
    return {"items": [{**r, "label": pipeline_runs.RUN_DA[r["status"]]} for r in rows]}


@router.get("/runs/{run_id}")
async def run_detail(run_id: int):
    async with db.connection() as conn:
        r = await db.fetch_one(conn, """select r.*, a.name, a.slug from dev_pipeline_runs r join dev_artifacts a on a.id = r.artifact_id
                                        where r.id = %s""", (run_id,))
        if r is None:
            raise HTTPException(404, "Kørslen findes ikke")
        phases = await db.fetch_all(conn, "select phase, label, started_at, ms, detail from dev_pipeline_run_phases where run_id = %s order by id",
                                    (run_id,))
    return {**r, "label": pipeline_runs.RUN_DA[r["status"]], "phases": phases}


@router.get("/snapshots")
async def snapshots():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select d.id, d.key, d.name, d.refreshed_at, d.generation, d.pipeline_artifact_id, a.slug,
                   v.major, v.minor, v.patch,
                   (select r.size_bytes from dev_pipeline_runs r where r.artifact_id = d.pipeline_artifact_id and r.status = 'succeeded'
                    order by r.id desc limit 1) as size_bytes,
                   (select r.rows from dev_pipeline_runs r where r.artifact_id = d.pipeline_artifact_id and r.status = 'succeeded'
                    order by r.id desc limit 1) as rows
            from datasets d left join dev_artifacts a on a.id = d.pipeline_artifact_id
            left join dev_artifact_versions v on v.id = a.current_version_id
            where d.kind = 'snapshot' order by d.name""")
        out = []
        for r in rows:
            used = await artifacts.dependents(conn, r["pipeline_artifact_id"]) if r["pipeline_artifact_id"] else []
            out.append({**r, "version": f"{r['major']}.{r['minor']}.{r['patch']}" if r["major"] is not None else None, "usedBy": used})
    return {"items": out}
