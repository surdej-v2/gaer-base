"""/api/admin/dev: the shared core of Gær Developer (specs/developer/) — overview, artifacts with owners and versions,
approvals, reviews, messages, the catalog with lineage, developers (roles) and the search behind ⌘K.

Access: admin section "/developer" (admin roles Udvikler, Udvikler-admin, Data, Fuld; app/admin_roles.py). Writes
that only Udvikler-admin may make are listed in admin_roles.DEV_ADMIN_WRITES. Every change is audited in the same
transaction by app/devkit/artifacts.py or here.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import admin_roles, db
from app.devkit import artifacts, assist, catalog
from app.logs import audit
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/dev", tags=["developer"], dependencies=[Depends(require_admin)])

STUDIOS = [
    {"key": "datastudie", "title": "Datastudie", "text": "Beskriv data med AI og udgiv som MCP-endpoint", "path": "/developer/datastudie",
     "types": ["operation", "multisource", "mcp_endpoint"]},
    {"key": "pipeline", "title": "Pipeline", "text": "Byg snapshots af data", "path": "/developer/pipeline", "types": ["pipeline"]},
    {"key": "mcp", "title": "MCP-studie", "text": "Test, adgang og drift", "path": "/developer/mcp", "types": ["mcp_endpoint"]},
    {"key": "api", "title": "API-studie", "text": "REST-API'er med OpenAPI og nøgler", "path": "/developer/api", "types": ["api", "api_key"]},
]


async def _counts(conn, user_id: int) -> dict:
    row = await db.fetch_one(conn, """
        select (select count(*) from dev_artifact_approvals a join dev_artifact_versions v on v.id = a.version_id
                where a.user_id = %s and a.decision = 'pending' and v.status = 'pending') as approvals,
               (select count(distinct r.id) from dev_reviews r join dev_artifact_roles o on o.artifact_id = r.artifact_id
                where r.status = 'open' and o.user_id = %s and o.role in ('A', 'R')) as reviews,
               (select count(*) from dev_notifications where user_id = %s and read_at is null) as unread""",
        (user_id, user_id, user_id))
    return dict(row)


@router.get("/me")
async def me(p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        counts = await _counts(conn, p.user_id)
    return {"userId": p.user_id, "roles": list(p.admin_roles), "devAdmin": admin_roles.is_dev_admin(p.admin_roles),
            "ai": assist.enabled(), "counts": counts}


@router.get("/overview")
async def overview(p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        by_type = {r["type"]: r for r in await db.fetch_all(conn, """
            select type, count(*) as n, count(*) filter (where status = 'review_required') as review,
                   count(*) filter (where status = 'disabled') as disabled from dev_artifacts group by type""")}
        counts = await _counts(conn, p.user_id)
        waiting = await db.fetch_all(conn, """
            select v.id as version_id, a.id as artifact_id, a.type, a.name, v.major, v.minor, v.patch, v.change_type, ap.role
            from dev_artifact_approvals ap join dev_artifact_versions v on v.id = ap.version_id
            join dev_artifacts a on a.id = v.artifact_id
            where ap.user_id = %s and ap.decision = 'pending' and v.status = 'pending' order by v.submitted_at limit 10""",
            (p.user_id,))
        reviews = await db.fetch_all(conn, """
            select distinct r.id, a.id as artifact_id, a.type, a.name, c.name as cause from dev_reviews r
            join dev_artifacts a on a.id = r.artifact_id join dev_artifacts c on c.id = r.cause_artifact_id
            join dev_artifact_roles o on o.artifact_id = a.id and o.user_id = %s and o.role in ('A', 'R')
            where r.status = 'open' order by r.id limit 10""", (p.user_id,))
        notes = await db.fetch_all(conn, """select id, kind, title, body, link, created_at, read_at from dev_notifications
                                            where user_id = %s order by id desc limit 8""", (p.user_id,))
        datasets = await db.fetch_one(conn, "select count(*) as n from datasets")
    studios = []
    for s in STUDIOS:
        studios.append({**s, "count": sum(by_type.get(t, {}).get("n", 0) for t in s["types"]),
                        "review": sum(by_type.get(t, {}).get("review", 0) for t in s["types"])})
    return {"studios": studios, "counts": counts, "datasets": datasets["n"],
            "connections": by_type.get("connection", {}).get("n", 0),
            "waiting": [{**w, "version": f"{w['major']}.{w['minor']}.{w['patch']}",
                         "typeLabel": artifacts.TYPE_LABELS[w["type"]]} for w in waiting],
            "reviews": reviews, "notifications": notes}


# ── Artifacts ───────────────────────────────────────────────────────────────

@router.get("/artifacts")
async def list_artifacts(type: str | None = None, q: str | None = None, limit: int = 200):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select {artifacts.COLUMNS}, v.major, v.minor, v.patch,
                   (select status from dev_artifact_versions o where o.artifact_id = a.id and o.status in ('draft', 'pending')) as open_status
            from dev_artifacts a left join dev_artifact_versions v on v.id = a.current_version_id
            where (%s::text is null or a.type = %s) and (%s::text is null or a.name ilike %s or a.slug ilike %s)
            order by a.type, a.name limit %s""",
            (type, type, q, f"%{q}%", f"%{q}%", max(1, min(limit, 500))))
    return {"items": [{**artifacts.out(r, r if r["major"] is not None else None), "open": r["open_status"]} for r in rows]}


@router.get("/artifacts/{artifact_id}")
async def artifact_detail(artifact_id: int):
    async with db.connection() as conn:
        return await artifacts.detail(conn, artifact_id)


class ArtifactPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    approval_flow: bool | None = None
    enabled: bool | None = None


async def _owner_or_dev_admin(conn, artifact_id: int, p: Principal, roles=("A",)) -> None:
    if admin_roles.is_dev_admin(p.admin_roles) or await artifacts.is_owner(conn, artifact_id, p.user_id, roles):
        return
    raise HTTPException(403, "Kun artefaktets Accountable eller en Udvikler-admin kan ændre det")


@router.patch("/artifacts/{artifact_id}")
async def update_artifact(artifact_id: int, body: ArtifactPatch, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        await _owner_or_dev_admin(conn, artifact_id, p, ("A", "R") if body.approval_flow is None else ("A",))
        values = {k: v for k, v in body.model_dump(exclude_none=True).items() if k != "enabled"}
        changed = {k: v for k, v in values.items() if a[k] != v}
        if changed:
            sets = ", ".join(f"{k} = %s" for k in changed)
            await conn.execute(f"update dev_artifacts set {sets}, updated_at = now() where id = %s", (*changed.values(), artifact_id))
            await audit(conn, "dev.artifact.updated", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=artifact_id,
                        before={k: a[k] for k in changed}, after=changed)
        if body.enabled is not None:
            await artifacts.set_status(conn, a, body.enabled, actor_id=p.user_id, meta=p.meta)
            if a["type"] == "mcp_endpoint":
                from app.devkit import mcp_endpoints
                await mcp_endpoints.set_enabled(conn, a, body.enabled)
        return await artifacts.detail(conn, artifact_id)


class RaciIn(BaseModel):
    assignments: list[dict] = Field(max_length=50)   # [{userId, roles: ["A", "R"]}]


@router.put("/artifacts/{artifact_id}/raci")
async def set_raci(artifact_id: int, body: RaciIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        await _owner_or_dev_admin(conn, artifact_id, p)
        wanted = {int(x["userId"]): list(x.get("roles") or []) for x in body.assignments if x.get("userId")}
        await artifacts.set_raci(conn, a, wanted, actor_id=p.user_id, meta=p.meta)
        return await artifacts.detail(conn, artifact_id)


class SubmitIn(BaseModel):
    note: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=2000)


@router.post("/artifacts/{artifact_id}/submit")
async def submit(artifact_id: int, body: SubmitIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        v = await artifacts.submit(conn, a, actor_id=p.user_id, meta=p.meta, note=body.note, reason=body.reason)
        return artifacts.version_out(v)


@router.delete("/artifacts/{artifact_id}/draft")
async def withdraw(artifact_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        v = await artifacts.open_version(conn, artifact_id)
        if v is None:
            raise HTTPException(404, "Der er ingen kladde")
        await conn.execute("update dev_artifact_versions set status = 'withdrawn', decided_at = now() where id = %s", (v["id"],))
        await audit(conn, "dev.version.withdrawn", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=a["id"],
                    version=artifacts.version_str(v))
    return {"ok": True}


# ── Approvals and reviews ──────────────────────────────────────────────────

@router.get("/approvals")
async def approvals(scope: str = "mine", p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        if scope == "mine":
            rows = await db.fetch_all(conn, f"""
                select {artifacts.VERSION_COLUMNS}, a.type, a.slug, a.name, ap.role from dev_artifact_approvals ap
                join dev_artifact_versions v on v.id = ap.version_id join dev_artifacts a on a.id = v.artifact_id
                where ap.user_id = %s and ap.decision = 'pending' and v.status = 'pending' order by v.submitted_at""",
                (p.user_id,))
        else:
            rows = await db.fetch_all(conn, f"""
                select {artifacts.VERSION_COLUMNS}, a.type, a.slug, a.name, null as role from dev_artifact_versions v
                join dev_artifacts a on a.id = v.artifact_id where v.status <> 'draft'
                order by coalesce(v.decided_at, v.submitted_at, v.created_at) desc limit 200""")
        names = await artifacts.people(conn, [r["created_by"] for r in rows])
    return {"items": [{**artifacts.version_out(r), "artifactId": r["artifact_id"], "type": r["type"],
                       "typeLabel": artifacts.TYPE_LABELS[r["type"]], "slug": r["slug"], "name": r["name"], "role": r["role"],
                       "author": names.get(r["created_by"], {}).get("name")} for r in rows]}


@router.get("/approvals/{version_id}")
async def approval_detail(version_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        v = await artifacts.version(conn, version_id)
        a = await artifacts.get(conn, v["artifact_id"])
        cur = await artifacts.current(conn, a)
        rows = await artifacts.approvals_for(conn, version_id)
        names = await artifacts.people(conn, [r["user_id"] for r in rows] + [v["created_by"]])
        raci = await artifacts.roles(conn, a["id"])
    roles_present = {r for rs in raci.values() for r in rs}
    return {"artifact": artifacts.out(a, cur), "version": {**artifacts.version_out(v), "definition": v["definition"],
                                                           "contract": v["contract"]},
            "from": artifacts.version_str(cur) if cur and cur["id"] != v["id"] else None,
            "previous": {"definition": cur["definition"], "contract": cur["contract"]} if cur and cur["id"] != v["id"] else None,
            "author": names.get(v["created_by"], {}).get("name"),
            "approvals": [{**r, "name": names.get(r["user_id"], {}).get("name"), "roleLabel": artifacts.ROLE_LABELS[r["role"]]}
                          for r in rows],
            "needs": list(artifacts.NEEDS[v["change_type"]]) if a["approval_flow"] else [],
            "informed": "I" in roles_present,
            "mine": any(r["user_id"] == p.user_id and r["decision"] == "pending" for r in rows)}


class DecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)


@router.post("/approvals/{version_id}")
async def decide(version_id: int, body: DecideIn, p: Principal = Depends(require_admin)):
    if not body.approve and not body.note.strip():
        raise HTTPException(422, "Skriv hvorfor du afviser")
    async with db.connection() as conn:
        v = await artifacts.decide(conn, version_id, actor_id=p.user_id, approve=body.approve, note=body.note, meta=p.meta)
    return artifacts.version_out(v)


class ReviewIn(BaseModel):
    note: str = Field(default="", max_length=2000)


@router.post("/reviews/{review_id}/approve")
async def approve_review(review_id: int, body: ReviewIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        return await artifacts.approve_review(conn, review_id, actor_id=p.user_id, note=body.note, meta=p.meta)


@router.get("/notifications")
async def notifications(p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select id, kind, title, body, link, artifact_id, created_at, read_at
                                           from dev_notifications where user_id = %s order by id desc limit 100""", (p.user_id,))
    return {"items": rows}


@router.post("/notifications/read")
async def read_notifications(p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await conn.execute("update dev_notifications set read_at = now() where user_id = %s and read_at is null", (p.user_id,))
    return {"ok": True}


# ── Catalog ─────────────────────────────────────────────────────────────────

@router.get("/catalog")
async def catalog_list():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select d.id, d.key, d.name, d.description, d.kind, d.classification, d.personal_data, d.artifact_id, d.refreshed_at,
                   d.updated_at, coalesce(s.name, case when d.kind = 'snapshot' then 'Pipeline' else 'Tørgær (egen database)' end) as source_name,
                   (select count(*) from dataset_fields f where f.dataset_id = d.id) as fields,
                   (select count(*) from dataset_fields f where f.dataset_id = d.id and f.personal_data) as personal_fields,
                   (select max(case f.classification when 'red' then 3 when 'yellow' then 2 when 'green' then 1 else 0 end)
                      from dataset_fields f where f.dataset_id = d.id) as max_rank,
                   a.status as artifact_status
            from datasets d left join data_sources s on s.id = d.source_id left join dev_artifacts a on a.id = d.artifact_id
            order by d.kind, d.name""")
    return {"items": [{**r, "kindLabel": catalog.KIND_LABELS.get(r["kind"], r["kind"])} for r in rows]}


@router.get("/catalog/datasets/{dataset_id}")
async def catalog_dataset(dataset_id: int):
    async with db.connection() as conn:
        d = await catalog.dataset(conn, dataset_id)
        fields = await catalog.fields(conn, dataset_id)
        lineage = await catalog.lineage(conn, dataset_id)
        art = await artifacts.detail(conn, d["artifact_id"]) if d["artifact_id"] else None
    d.pop("spec_yaml", None)
    return {"dataset": {**d, "kindLabel": catalog.KIND_LABELS.get(d["kind"], d["kind"])}, "fields": fields,
            "lineage": lineage, "artifact": art}


class FieldEdit(BaseModel):
    name: str = Field(max_length=63)
    label: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    classification: str | None = Field(default=None, pattern="^(white|green|yellow|red)$")
    personal_data: bool | None = None
    sensitive: bool | None = None
    mask: str | None = Field(default=None, max_length=20)


class CatalogEdit(BaseModel):
    description: str | None = Field(default=None, max_length=4000)
    fields: list[FieldEdit] = Field(default_factory=list, max_length=200)
    note: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=2000)


@router.put("/catalog/datasets/{dataset_id}")
async def edit_catalog(dataset_id: int, body: CatalogEdit, p: Principal = Depends(require_admin)):
    """F-5: a change to the catalog is a new version of the dataset's artifact and follows the approval flow."""
    async with db.connection() as conn:
        d = await catalog.dataset(conn, dataset_id)
        a = await catalog.ensure_artifact(conn, d, actor_id=p.user_id, meta=p.meta)
        cur = await artifacts.current(conn, a)
        base = cur["definition"] if cur else await catalog.definition_of(conn, d)
        new_fields = catalog.clean_fields(base["fields"], [f.model_dump() for f in body.fields]) if body.fields else base["fields"]
        definition = {**base, "description": body.description if body.description is not None else base.get("description", ""),
                      "fields": new_fields}
        await artifacts.save_draft(conn, a, definition, actor_id=p.user_id, meta=p.meta, note=body.note)
        v = await artifacts.submit(conn, a, actor_id=p.user_id, meta=p.meta, note=body.note, reason=body.reason)
        return {"version": artifacts.version_out(v), "artifactId": a["id"]}


@router.post("/catalog/datasets/{dataset_id}/suggest")
async def suggest(dataset_id: int, p: Principal = Depends(require_admin)):
    """F-8: classification suggestions for fields that are still at the default (yellow, no description)."""
    async with db.connection() as conn:
        fields = await catalog.fields(conn, dataset_id)
        known = [f for f in await catalog.all_fields(conn) if f["classification"] != "yellow" or f["personal_data"]]
    out = []
    for f in fields:
        if f["classification"] == "yellow" and not f["description"] and not f["personal_data"]:
            s = await assist.suggest_classification({"name": f["name"], "label": f["label"], "data_type": f["data_type"]},
                                                    known, p.user_id)
            if s["classification"] != "yellow" or s["personal_data"]:
                out.append({"field": f["name"], **s})
    return {"suggestions": out, "ai": assist.enabled()}


# ── Developers (roles) ─────────────────────────────────────────────────────

@router.get("/people")
async def people(q: str = ""):
    """Internal users for the RACI picker (name and id only)."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select id, display_name, email, role, admin_roles from users
            where audience = 'internal' and blocked_at is null and not impersonation_only
              and (%s = '' or display_name ilike %s or email ilike %s) order by display_name nulls last, id limit 30""",
            (q, f"%{q}%", f"%{q}%"))
    return {"items": [{"id": r["id"], "name": r["display_name"] or (r["email"] or "").split("@")[0] or f"#{r['id']}",
                       "developer": r["role"] == "admin" and admin_roles.is_developer(r["admin_roles"])} for r in rows]}


@router.get("/developers")
async def developers():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select id, display_name, email, admin_roles from users
                                           where role = 'admin' and admin_roles && array['udvikler', 'udvikler_admin', 'data', 'fuld']
                                           order by display_name nulls last, id""")
    return {"items": [{"id": r["id"], "name": r["display_name"] or (r["email"] or "").split("@")[0],
                       "roles": [x for x in r["admin_roles"] if x in admin_roles.DEVELOPERS]} for r in rows]}


class DeveloperIn(BaseModel):
    role: str = Field(pattern="^(none|udvikler|udvikler_admin)$")


@router.put("/developers/{user_id}")
async def set_developer(user_id: int, body: DeveloperIn, p: Principal = Depends(require_admin_stepup)):
    """Udvikler-admin gives or takes the role Udvikler; only Fuld gives Udvikler-admin. External users never get them.
    A plain user who gets Udvikler becomes an admin with only that role (and loses admin again when it is taken)."""
    if body.role == "udvikler_admin" and admin_roles.FULL not in p.admin_roles:
        raise HTTPException(403, "Kun en admin med Fuld adgang kan give rollen Udvikler-admin")
    if user_id == p.user_id:
        raise HTTPException(409, "Du kan ikke ændre dine egne roller")
    async with db.connection() as conn:
        u = await db.fetch_one(conn, """select id, role, admin_roles, audience, super_admin, impersonation_only from users
                                        where id = %s for update""", (user_id,))
        if u is None or u["impersonation_only"]:
            raise HTTPException(404, "Brugeren findes ikke")
        if u["audience"] != "internal":
            raise HTTPException(422, "Eksterne brugere kan ikke få udviklerroller")
        current = [r for r in u["admin_roles"] if r not in ("udvikler", "udvikler_admin")] if u["role"] == "admin" else []
        if admin_roles.FULL in current and admin_roles.FULL not in p.admin_roles:
            raise HTTPException(403, "Kun en admin med Fuld adgang kan ændre en anden med Fuld adgang")
        if "udvikler_admin" in u["admin_roles"] and admin_roles.FULL not in p.admin_roles:
            raise HTTPException(403, "Kun en admin med Fuld adgang kan ændre en Udvikler-admin")
        new_roles = current + ([body.role] if body.role != "none" else [])
        new_role = "admin" if new_roles else "user"
        await conn.execute("update users set role = %s, admin_roles = %s where id = %s", (new_role, new_roles, user_id))
        await audit(conn, "role.changed", actor_id=p.user_id, meta=p.meta, table="users", row_id=user_id,
                    before={"role": u["role"], "admin_roles": u["admin_roles"]}, after={"role": new_role, "admin_roles": new_roles},
                    via="developer")
    return {"ok": True, "role": new_role, "adminRoles": new_roles}


# ── Search (⌘K) ─────────────────────────────────────────────────────────────

@router.get("/search")
async def search(q: str = ""):
    q = q.strip()
    if not q:
        return {"items": []}
    like = f"%{q}%"
    async with db.connection() as conn:
        arts = await db.fetch_all(conn, """select id, type, slug, name from dev_artifacts
                                           where name ilike %s or slug ilike %s order by name limit 15""", (like, like))
        sets = await db.fetch_all(conn, """select id, name, key, kind from datasets where name ilike %s or key ilike %s
                                           order by name limit 10""", (like, like))
    return {"items": [{"kind": "artifact", "type": a["type"], "label": a["name"], "hint": artifacts.TYPE_LABELS[a["type"]],
                       "path": f"/developer/artefakter/{a['id']}"} for a in arts]
            + [{"kind": "dataset", "label": d["name"], "hint": f"Datasæt · {catalog.KIND_LABELS.get(d['kind'], d['kind'])}",
                "path": f"/developer/katalog/{d['id']}"} for d in sets]}
