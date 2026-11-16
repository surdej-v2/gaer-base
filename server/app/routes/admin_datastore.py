"""/api/admin/store (section /data/store): governing the data store. Rules in app/datastore.py.

Every admin with the section sees every group container's metadata (never another user's personal one); the items
are read and written through /api/store with the admin's own access. Creating a group container, changing its
settings, RACI or shares, archiving it and answering a release need the right role in the container — and, except for
creating, a second factor within the step-up window.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import admin_roles, db, groups
from app import datastore as ds
from app.logs import audit
from app.routes.datastore import SettingsIn, SharesPut, release_out, summary
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/store", tags=["admin"], dependencies=[Depends(require_admin)])


class RaciIn(BaseModel):
    user_id: int
    roles: list[str] = Field(min_length=1, max_length=4)


class AdminSettingsIn(SettingsIn):
    minor_rule: str | None = Field(default=None, pattern="^(auto|one_r|a)$")
    major_rule: str | None = Field(default=None, pattern="^(a_and_all_c|a_and_one_c|a_only)$")


class CreateIn(BaseModel):
    kind: str = Field(pattern="^(nosql|kv)$")
    group_id: int | None = None          # none = a personal container for you
    slug: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    visibility: str = Field(default="private", pattern="^(private|shared)$")
    versioning: str = Field(default="semver", pattern="^(none|revisions|semver)$")
    minor_rule: str = Field(default="one_r", pattern="^(auto|one_r|a)$")
    major_rule: str = Field(default="a_and_all_c", pattern="^(a_and_all_c|a_and_one_c|a_only)$")
    sunset_days: int = Field(default=90, ge=0, le=3650)
    json_schema: dict | None = None
    default_ttl_seconds: int | None = Field(default=None, ge=1, le=31536000)
    raci: list[RaciIn] | None = Field(default=None, max_length=200)
    shares: SharesPut | None = None


class RaciPut(BaseModel):
    raci: list[RaciIn] = Field(min_length=1, max_length=200)


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _raci_dict(items: list[RaciIn]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in items:
        out.setdefault(r.user_id, []).extend(r.roles)
    return out


def _full(p: Principal) -> bool:
    return p.super_admin or admin_roles.FULL in p.admin_roles


async def _group_container(conn, container_id: int, admin: Principal, need: str | None = None) -> tuple[dict, dict, str | None]:
    c = await ds.get(conn, container_id)
    me = await ds.user_row(conn, admin.user_id)
    if c["owner_id"] is not None and c["owner_id"] != admin.user_id:
        raise HTTPException(404, "Samlingen findes ikke")
    level = await ds.level_of(conn, c, me)
    if need and (level is None or ds.LEVELS.index(level) < ds.LEVELS.index(need)):
        raise HTTPException(403, "Kun den ansvarlige (A) kan ændre det" if need == "manage" else "Du har ikke adgang til det")
    return c, me, level


# ── Lists and meta ─────────────────────────────────────────────────────────

@router.get("/meta")
async def meta(admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        all_groups = await db.fetch_all(conn, f"select {groups.COLUMNS} from groups g order by lower(g.name)")
        members = await db.fetch_all(conn, "select group_id, user_id, role from group_members")
        users = await ds.users_by_id(conn, [m["user_id"] for m in members])
        people = [ds.person(u) for u in await ds.eligible_admins(conn)]
    by_group: dict[int, list] = {}
    for m in members:
        by_group.setdefault(m["group_id"], []).append({**ds.person(users[m["user_id"]]), "group_role": m["role"]})
    return {
        "groups": [{**g, "members": by_group.get(g["id"], []),
                    "is_member": any(m["id"] == admin.user_id for m in by_group.get(g["id"], []))} for g in all_groups],
        "people": people,
        "me": admin.user_id,
        "full": _full(admin),
        "kinds": [{"key": k, "label": v} for k, v in ds.KINDS.items()],
        "versioning": [{"key": k, "label": v} for k, v in ds.VERSIONING.items()],
        "minor_rules": [{"key": k, "label": v} for k, v in ds.MINOR_RULES.items()],
        "major_rules": [{"key": k, "label": v} for k, v in ds.MAJOR_RULES.items()],
        "share_levels": [{"key": k, "label": v} for k, v in ds.SHARE_LEVELS.items()],
    }


@router.get("")
async def list_containers(archived: bool = False, admin: Principal = Depends(require_admin)):
    """Every group container, and your own personal ones."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select {ds.CONTAINER_COLUMNS},
                   (select r.user_id from store_raci r where r.container_id = c.id and r.role = 'A') as a_id,
                   (select array_agg(r.role) from store_raci r where r.container_id = c.id and r.user_id = %(u)s) as my_roles,
                   (select count(*) from store_items i where i.container_id = c.id) as items,
                   (select count(*) from store_shares s where s.container_id = c.id) as share_count,
                   (select r.major || '.' || r.minor from store_releases r where r.container_id = c.id
                      and r.status = 'pending') as pending_version,
                   (select r.kind from store_releases r where r.container_id = c.id and r.status = 'pending') as pending_kind
            from store_containers c left join groups g on g.id = c.group_id
            where (c.archived_at is null) <> %(archived)s and (c.group_id is not null or c.owner_id = %(u)s)
            order by c.owner_id is not null, lower(c.name)""", {"u": admin.user_id, "archived": archived})
        users = await ds.users_by_id(conn, [r["a_id"] for r in rows])
        me = await ds.user_row(conn, admin.user_id)
        items = []
        for r in rows:
            a = users.get(r["a_id"])
            items.append({**{k: r[k] for k in r if k not in ("a_id", "json_schema", "my_roles")},
                          "ref": f"~{r['slug']}" if r["owner_id"] is not None else r["slug"],
                          "personal": r["owner_id"] is not None,
                          "current_version": ds.version_label(r["current_major"], r["current_minor"]),
                          "my_roles": sorted(r["my_roles"] or [], key=ds.RACI.index),
                          "my_level": await ds.level_of(conn, r, me),
                          "accountable": ds.person(a),
                          "status": ds.status_of(r, has_a=a is not None, pending=1 if r["pending_version"] else 0)})
        waiting = await ds.waiting_for(conn, me)
    return {"items": items, "waiting": [{**{k: w[k] for k in ("id", "container_id", "major", "minor", "kind", "created_at",
                                                               "container_name", "slug", "fallback")},
                                         "version": f"{w['major']}.{w['minor']}",
                                         "breaking": len(w["breaking"] or [])} for w in waiting]}


@router.post("", status_code=201)
async def create(body: CreateIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        if body.group_id is not None and not _full(admin) and not await groups.is_member(conn, body.group_id, admin.user_id):
            raise HTTPException(403, "Du kan kun oprette samlinger for grupper, du er medlem af")
        c = await ds.create(conn, kind=body.kind, slug=body.slug, name=body.name, description=body.description,
                            group_id=body.group_id, owner_id=None if body.group_id is not None else admin.user_id,
                            visibility=body.visibility, versioning=body.versioning, minor_rule=body.minor_rule,
                            major_rule=body.major_rule, sunset_days=body.sunset_days, json_schema=body.json_schema,
                            default_ttl_seconds=body.default_ttl_seconds, actor_id=admin.user_id,
                            raci=_raci_dict(body.raci) if body.raci else None, meta=admin.meta)
        if body.shares and body.shares.shares:
            await ds.write_shares(conn, c["id"], [s.model_dump() for s in body.shares.shares], admin.user_id)
            await audit(conn, "store.shares_changed", actor_id=admin.user_id, meta=admin.meta, table=ds.TABLE,
                        row_id=c["id"], shares=[s.model_dump() for s in body.shares.shares])
        return await summary(conn, c, await ds.user_row(conn, admin.user_id), "manage")


@router.get("/waiting")
async def waiting(admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        me = await ds.user_row(conn, admin.user_id)
        return {"items": [await release_out(conn, r, me) | {"container_name": r["container_name"]}
                          for r in await ds.waiting_for(conn, me)]}


@router.get("/{container_id}")
async def detail(container_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        c, me, level = await _group_container(conn, container_id, admin)
        out = await summary(conn, c, me, level)
        raci = await ds.raci_map(conn, c["id"])
        users = await ds.users_by_id(conn, list(raci) + [c["created_by"]])
        pending = await db.fetch_one(conn, f"""select {ds.RELEASE_COLUMNS} from store_releases r
                                               where r.container_id = %s and r.status = 'pending'""", (c["id"],))
        return {**out, "minor_rule_label": ds.MINOR_RULES[c["minor_rule"]], "major_rule_label": ds.MAJOR_RULES[c["major_rule"]],
                "raci": [{**ds.person(users[u]), "roles": roles} for u, roles in raci.items() if u in users],
                "my_roles": sorted(raci.get(admin.user_id, []), key=ds.RACI.index),
                "shares": await ds.shares(conn, c["id"]),
                "created_by": ds.person(users.get(c["created_by"])),
                "pending": await release_out(conn, pending, me) if pending else None,
                "status": ds.status_of(c, has_a=any("A" in r for r in raci.values()), pending=1 if pending else 0)}


@router.patch("/{container_id}")
async def update(container_id: int, body: AdminSettingsIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        c, me, level = await _group_container(conn, container_id, admin, "manage")
        c = await ds.update_settings(conn, c, body.model_dump(exclude_unset=True), actor_id=admin.user_id, meta=admin.meta)
        return await summary(conn, c, me, level)


@router.put("/{container_id}/raci")
async def put_raci(container_id: int, body: RaciPut, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        c, _, _ = await _group_container(conn, container_id, admin, "manage")
        if c["group_id"] is None:
            raise HTTPException(400, "En personlig samling har ingen RACI")
        before = await ds.raci_map(conn, c["id"])
        after = await ds.validate_raci(conn, _raci_dict(body.raci))
        await ds.write_raci(conn, c["id"], after)
        await audit(conn, "store.raci_changed", actor_id=admin.user_id, meta=admin.meta, table=ds.TABLE, row_id=c["id"],
                    before={str(k): v for k, v in before.items()}, after={str(k): v for k, v in after.items()})
        return {"raci": [{"user_id": u, "roles": r} for u, r in after.items()]}


@router.put("/{container_id}/shares")
async def put_shares(container_id: int, body: SharesPut, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        c, _, _ = await _group_container(conn, container_id, admin, "manage")
        before = await ds.shares(conn, c["id"])
        await ds.write_shares(conn, c["id"], [s.model_dump() for s in body.shares], admin.user_id)
        after = await ds.shares(conn, c["id"])
        await audit(conn, "store.shares_changed", actor_id=admin.user_id, meta=admin.meta, table=ds.TABLE, row_id=c["id"],
                    before=[{"group_id": s["group_id"], "level": s["level"]} for s in before],
                    after=[{"group_id": s["group_id"], "level": s["level"]} for s in after])
        return {"shares": after}


@router.post("/{container_id}/archive")
async def archive(container_id: int, body: ReasonIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        c, _, _ = await _group_container(conn, container_id, admin, "manage")
        await ds.archive(conn, c, actor_id=admin.user_id, meta=admin.meta, reason=body.reason)
    return {"ok": True}


@router.post("/versions/{release_id}/decide")
async def decide(release_id: int, body: DecideIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        rel = await ds.decide(conn, release_id, user_id=admin.user_id, approve=body.approve, note=body.note, meta=admin.meta)
        return await release_out(conn, rel, await ds.user_row(conn, admin.user_id))
