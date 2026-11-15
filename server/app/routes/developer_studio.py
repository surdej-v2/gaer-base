"""/api/admin/dev: Datastudie (DS-3 … DS-9) — operations, the guide that makes an MCP endpoint, live samples with the
masking the catalog asks for — and the cache (F-21). Every change is a version of an artifact (app/devkit/artifacts.py)
and audited there.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.devkit import artifacts, assist, cache, masking, mcp_endpoints, multisource, operations
from app.mcp import endpoints as endpoints_mod
from app.mcp import oauth
from app.sessions import Principal, require_admin

router = APIRouter(prefix="/api/admin/dev", tags=["developer"], dependencies=[Depends(require_admin)])


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return s if re.match(r"^[a-z]", s) else f"op_{s}"


# ── Operations ──────────────────────────────────────────────────────────────

@router.get("/operations")
async def list_operations():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select {artifacts.COLUMNS}, v.major, v.minor, v.patch, v.definition from dev_artifacts a
                                            left join dev_artifact_versions v on v.id = a.current_version_id
                                            where a.type = 'operation' order by a.name""")
        used = await db.fetch_all(conn, """select t.artifact_id, t.name, e.endpoint from mcp_tools t
                                           join mcp_tool_endpoints e on e.tool_id = t.id where t.kind = 'operation'""")
    by_op: dict[int, list] = {}
    for u in used:
        by_op.setdefault(u["artifact_id"], []).append({"tool": u["name"], "endpoint": u["endpoint"]})
    return {"items": [{**artifacts.out(r, r if r["major"] is not None else None),
                       "dataset": (r["definition"] or {}).get("_dataset"), "fields": len((r["definition"] or {}).get("fields") or []),
                       "params": [p["name"] for p in (r["definition"] or {}).get("params") or []], "usedBy": by_op.get(r["id"], [])}
                      for r in rows]}


class OperationIn(BaseModel):
    slug: str = Field(min_length=1, max_length=62)
    name: str = Field(min_length=1, max_length=120)
    definition: dict
    approval_flow: bool = False
    publish: bool = True


@router.post("/operations", status_code=201)
async def create_operation(body: OperationIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        d = await operations.validate(conn, body.definition)
        a = await artifacts.create(conn, type_="operation", slug=_slug(body.slug), name=body.name, description=d["description"],
                                   definition=d, actor_id=p.user_id, meta=p.meta, approval_flow=body.approval_flow, publish=body.publish)
        return await artifacts.detail(conn, a["id"])


@router.get("/operations/{artifact_id}")
async def operation_detail(artifact_id: int):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        if a["type"] != "operation":
            raise HTTPException(404, "Operationen findes ikke")
        return await artifacts.detail(conn, artifact_id)


class OperationEdit(BaseModel):
    definition: dict
    note: str = Field(default="", max_length=2000)
    submit: bool = False
    reason: str = Field(default="", max_length=2000)


@router.put("/operations/{artifact_id}")
async def save_operation(artifact_id: int, body: OperationEdit, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        if a["type"] != "operation":
            raise HTTPException(404, "Operationen findes ikke")
        d = await operations.validate(conn, body.definition)
        v = await artifacts.save_draft(conn, a, d, actor_id=p.user_id, meta=p.meta, note=body.note)
        if body.submit:
            v = await artifacts.submit(conn, a, actor_id=p.user_id, meta=p.meta, note=body.note, reason=body.reason)
    return {"version": artifacts.version_out(v)}


class TryIn(BaseModel):
    args: dict = Field(default_factory=dict)
    draft: bool = False
    cache: bool = True


@router.post("/operations/{artifact_id}/try")
async def try_operation(artifact_id: int, body: TryIn, p: Principal = Depends(require_admin)):
    """MCP-9 / API-10: runs the operation as the developer, against the published version or the draft. Masked."""
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        v = await artifacts.open_version(conn, artifact_id) if body.draft else await artifacts.current(conn, a)
    if v is None:
        raise HTTPException(409, "Der er ingen version at teste" if not body.draft else "Der er ingen kladde")
    return await operations.execute(a, v, body.args, principal=p, user_id=p.user_id, use_cache=body.cache)


@router.post("/operations/{artifact_id}/cache/clear")
async def clear_cache(artifact_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await artifacts.get(conn, artifact_id)
    gen = await cache.clear_artifact(artifact_id)
    async with db.connection() as conn:
        from app.logs import audit
        await audit(conn, "dev.cache.cleared", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=artifact_id, generation=gen)
    return {"generation": gen}


@router.get("/cache")
async def cache_status():
    return await cache.stats()


# ── Datastudie ──────────────────────────────────────────────────────────────

@router.get("/datastudie/endpoints")
async def list_endpoints():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select {artifacts.COLUMNS}, v.major, v.minor, v.patch, v.definition,
                                            (select status from dev_artifact_versions o where o.artifact_id = a.id and o.status in ('draft', 'pending')) as open_status
                                            from dev_artifacts a left join dev_artifact_versions v on v.id = a.current_version_id
                                            where a.type = 'mcp_endpoint' order by a.name""")
        roles = {r["artifact_id"]: r["names"] for r in await db.fetch_all(conn, """
            select r.artifact_id, array_agg(distinct coalesce(u.display_name, split_part(u.email, '@', 1))) as names
            from dev_artifact_roles r join users u on u.id = r.user_id where r.role in ('A', 'R') group by r.artifact_id""")}
    return {"items": [{**artifacts.out(r, r if r["major"] is not None else None), "url": oauth.resource_url(r["slug"]),
                       "audience": (r["definition"] or {}).get("audience"), "tools": len((r["definition"] or {}).get("tools") or []),
                       "owners": roles.get(r["id"], []), "open": r["open_status"]} for r in rows],
            "origin": oauth.origin()}


class NewEndpoint(BaseModel):
    slug: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=80)


@router.post("/datastudie/endpoints", status_code=201)
async def new_endpoint(body: NewEndpoint, p: Principal = Depends(require_admin)):
    """DS-3: the name gives a unique slug; the endpoint will answer on /mcp/<slug>. The guide is saved as a draft."""
    slug = body.slug.strip().lower()
    if not endpoints_mod.valid_key(slug):
        raise HTTPException(422, "Navnet må kun have små bogstaver, tal og bindestreg, skal starte med et bogstav og må ikke være reserveret")
    async with db.connection() as conn:
        if await endpoints_mod.get(conn, slug) or await artifacts.by_slug(conn, "mcp_endpoint", slug):
            raise HTTPException(409, f"/mcp/{slug} er allerede taget")
        definition = await mcp_endpoints.prepare(conn, None, {"name": body.name, "audience": "internal", "guide": {"step": 2}, "tools": []})
        a = await artifacts.create(conn, type_="mcp_endpoint", slug=slug, name=body.name, definition=definition, actor_id=p.user_id,
                                   meta=p.meta, publish=False)
    return {"id": a["id"], "url": oauth.resource_url(slug)}


@router.get("/datastudie/endpoints/{artifact_id}")
async def endpoint_detail(artifact_id: int):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        if a["type"] != "mcp_endpoint":
            raise HTTPException(404, "Endpointet findes ikke")
        detail = await artifacts.detail(conn, artifact_id)
        working = detail["draft"] or detail["current"]
        live = await endpoints_mod.get(conn, a["slug"])
    tools = []
    for t in working["definition"].get("tools") or []:
        out = (t.get("_contract") or {}).get("fields") or {}
        rank = max([masking.RANK[f["classification"]] for f in out.values()] or [0])
        tools.append({**{k: v for k, v in t.items() if k != "_contract"}, "fieldLevel": ["white", "green", "yellow", "red"][rank]})
    return {"artifact": detail, "definition": {k: v for k, v in working["definition"].items() if k != "tools"}, "tools": tools,
            "url": oauth.resource_url(a["slug"]), "live": bool(live and live["enabled"]),
            "connect": {"url": oauth.resource_url(a["slug"]), "metadata": f"{oauth.origin()}/.well-known/oauth-protected-resource/mcp/{a['slug']}"}}


class EndpointEdit(BaseModel):
    definition: dict
    note: str = Field(default="", max_length=2000)


@router.put("/datastudie/endpoints/{artifact_id}")
async def save_endpoint(artifact_id: int, body: EndpointEdit, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        if a["type"] != "mcp_endpoint":
            raise HTTPException(404, "Endpointet findes ikke")
        definition = await mcp_endpoints.prepare(conn, a, body.definition)
        v = await artifacts.save_draft(conn, a, definition, actor_id=p.user_id, meta=p.meta, note=body.note)
    return {"version": artifacts.version_out(v)}


class SourcesIn(BaseModel):
    description: str = Field(min_length=3, max_length=4000)


@router.post("/datastudie/sources")
async def suggest_sources(body: SourcesIn, p: Principal = Depends(require_admin)):
    """DS-5 / PL-16: datasets from the catalog with a relevance; snapshots first (with their age)."""
    async with db.connection() as conn:
        sets = await db.fetch_all(conn, """select d.id, d.key, d.name, d.description, d.kind, d.refreshed_at, d.personal_data,
                                           coalesce(s.name, case when d.kind = 'snapshot' then 'Pipeline' else 'Tørgær' end) as source_name,
                                           array(select f.name from dataset_fields f where f.dataset_id = d.id order by f.position) as field_names
                                           from datasets d left join data_sources s on s.id = d.source_id""")
    found = await assist.suggest_sources(body.description, sets, p.user_id)
    by_id = {s["id"]: s for s in sets}
    out = []
    for f in found:
        s = by_id[f["id"]]
        out.append({**f, "kind": s["kind"], "source": s["source_name"], "personal": s["personal_data"], "refreshedAt": s["refreshed_at"],
                    "fields": s["field_names"]})
    out.sort(key=lambda x: (x["relevance"] != "Høj relevans", x["kind"] != "snapshot"))
    return {"items": out, "ai": assist.enabled(), "all": [{"id": s["id"], "name": s["name"], "kind": s["kind"], "source": s["source_name"]} for s in sets]}


class SampleIn(BaseModel):
    dataset_id: int
    fields: list[str] = Field(default_factory=list, max_length=100)
    masking: dict = Field(default_factory=dict)


@router.post("/datastudie/sample")
async def sample(body: SampleIn, p: Principal = Depends(require_admin)):
    """DS-7: sample rows by a live lookup, masked, with the suggested masking per field from its classification."""
    async with db.connection() as conn:
        d = await operations.validate(conn, {"source": {"dataset": body.dataset_id}, "fields": body.fields or None,
                                             "masking": body.masking, "limit": 5})
    fake_artifact = {"id": 0}
    fake_version = {"id": 0, "status": "draft", "definition": d, "major": 0, "minor": 0, "patch": 0}
    out = await operations.execute(fake_artifact, fake_version, {}, principal=p, use_cache=False)
    suggestions = []
    for o in d["_output"]:
        f = {"classification": o["classification"], "personal_data": o["personal"]}
        suggestions.append({"field": o["name"], "classification": o["classification"], "personal": o["personal"],
                            "suggested": masking.default_for(f), "allowed": masking.allowed_for(f), "chosen": o["policy"]})
    return {"rows": out["rows"], "columns": out["columns"], "suggestions": suggestions}


class GenerateSource(BaseModel):
    dataset_id: int
    fields: list[str] = Field(default_factory=list, max_length=100)
    params: list[dict] = Field(default_factory=list, max_length=20)
    masking: dict = Field(default_factory=dict)
    tool: str | None = Field(default=None, max_length=48)
    description: str = Field(default="", max_length=1000)
    level: str = Field(default="white", pattern="^(white|green|yellow|red)$")


class GenerateIn(BaseModel):
    sources: list[GenerateSource] = Field(min_length=1, max_length=20)
    description: str = Field(default="", max_length=4000)
    instructions: str = Field(default="", max_length=4000)


@router.post("/datastudie/endpoints/{artifact_id}/generate")
async def generate(artifact_id: int, body: GenerateIn, p: Principal = Depends(require_admin)):
    """DS-8: one operation per chosen dataset (published with the endpoint's approval flow) and one tool each in the
    endpoint's draft. The draft is then sent for approval with /artifacts/{id}/submit."""
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        if a["type"] != "mcp_endpoint":
            raise HTTPException(404, "Endpointet findes ikke")
        working = await artifacts.open_version(conn, artifact_id) or await artifacts.current(conn, a)
        current_tools = [t for t in (working["definition"].get("tools") or [])]
        for src in body.sources:
            d = await operations.validate(conn, {"source": {"dataset": src.dataset_id}, "fields": src.fields or None, "params": src.params,
                                                 "masking": src.masking, "description": src.description})
            base = _slug(f"{a['slug']}_{d['_dataset']['key'].split('.')[-1]}")[:56]
            slug, n = base, 1
            while await artifacts.by_slug(conn, "operation", slug):
                n += 1
                slug = f"{base[:52]}_{n}"
            op = await artifacts.create(conn, type_="operation", slug=slug, name=f"{a['name']}: {d['_dataset']['name']}",
                                        description=d["description"], definition=d, actor_id=p.user_id, meta=p.meta,
                                        approval_flow=a["approval_flow"])
            tool = src.tool or _slug(f"soeg_{d['_dataset']['key'].split('.')[-1]}")[:48]
            current_tools = [t for t in current_tools if t["name"] != tool] + [
                {"name": tool, "operation": op["id"], "description": src.description or f"Data fra {d['_dataset']['name']}",
                 "level": src.level}]
        definition = await mcp_endpoints.prepare(conn, a, {**working["definition"], "description": body.description or working["definition"].get("description"),
                                                           "instructions": body.instructions or working["definition"].get("instructions"),
                                                           "tools": current_tools, "guide": {**(working["definition"].get("guide") or {}), "step": 5}})
        v = await artifacts.save_draft(conn, a, definition, actor_id=p.user_id, meta=p.meta)
    return {"version": artifacts.version_out(v)}


class ToolCheckIn(BaseModel):
    tool: dict


@router.post("/datastudie/check-tool")
async def check_tool(body: ToolCheckIn, p: Principal = Depends(require_admin)):
    """MCP-7: is the description clear enough? AI when it is on, a simple check otherwise."""
    return await assist.check_description(body.tool, p.user_id)


@router.get("/datastudie/datasets/{dataset_id}/fields")
async def dataset_fields(dataset_id: int):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select name, label, data_type, classification, personal_data, mask from dataset_fields
                                           where dataset_id = %s order by position, id""", (dataset_id,))
    return {"items": [{**r, "suggested": masking.default_for(r), "allowed": masking.allowed_for(r)} for r in rows]}



# ── Multisource sources (DS-10 … DS-17) ─────────────────────────────────────

@router.get("/multisource")
async def list_multisource():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select {artifacts.COLUMNS}, v.major, v.minor, v.patch, v.definition from dev_artifacts a
                                            left join dev_artifact_versions v on v.id = a.current_version_id
                                            where a.type = 'multisource' order by a.name""")
    return {"items": [{**artifacts.out(r, r if r["major"] is not None else None),
                       "steps": [s["id"] for s in (r["definition"] or {}).get("steps") or []]} for r in rows]}


class MultiIn(BaseModel):
    slug: str = Field(min_length=1, max_length=62)
    name: str = Field(min_length=1, max_length=120)
    definition: dict
    approval_flow: bool = False


@router.post("/multisource", status_code=201)
async def create_multisource(body: MultiIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        d = await multisource.validate(conn, body.definition)
        a = await artifacts.create(conn, type_="multisource", slug=_slug(body.slug), name=body.name, description=d["description"],
                                   definition=d, actor_id=p.user_id, meta=p.meta, approval_flow=body.approval_flow)
        return await artifacts.detail(conn, a["id"])


@router.get("/multisource/{artifact_id}")
async def multisource_detail(artifact_id: int):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        if a["type"] != "multisource":
            raise HTTPException(404, "Kilden findes ikke")
        detail = await artifacts.detail(conn, artifact_id)
    working = detail["draft"] or detail["current"]
    return {"artifact": detail, "levels": [[s["id"] for s in lvl] for lvl in multisource.levels(working["definition"]["steps"])]}


@router.put("/multisource/{artifact_id}")
async def save_multisource(artifact_id: int, body: OperationEdit, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        if a["type"] != "multisource":
            raise HTTPException(404, "Kilden findes ikke")
        d = await multisource.validate(conn, body.definition)
        v = await artifacts.save_draft(conn, a, d, actor_id=p.user_id, meta=p.meta, note=body.note)
        if body.submit:
            v = await artifacts.submit(conn, a, actor_id=p.user_id, meta=p.meta, note=body.note, reason=body.reason)
    return {"version": artifacts.version_out(v)}


@router.post("/multisource/{artifact_id}/try")
async def try_multisource(artifact_id: int, body: TryIn, p: Principal = Depends(require_admin)):
    """DS-16: every step with sample rows (masked), time, calls and cache status."""
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        v = await artifacts.open_version(conn, artifact_id) if body.draft else await artifacts.current(conn, a)
    if v is None:
        raise HTTPException(409, "Der er ingen version at teste")
    if not body.cache:
        await cache.clear_artifact(artifact_id)
    return await multisource.execute(a, v, body.args, principal=p, user_id=p.user_id)


@router.post("/multisource/{artifact_id}/cache/clear")
async def clear_multisource_cache(artifact_id: int, p: Principal = Depends(require_admin)):
    return await clear_cache(artifact_id, p)
