"""/api/store: the data store as the logged-in user (rules in app/datastore.py).

A container is addressed by id, by its slug (group containers) or "~slug" (your own), optionally pinned to a major or
a version: `kunder@3`, `kunder@3.2`, or `?version=draft|current|3|3.2`. Reads answer 404 for containers you cannot read.
Writes go to the working copy and need write access; releases are proposed here and decided in /admin/data/store.
Group containers are created and governed in /admin/data/store; personal ones ("~slug") here, by their owner.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import datastore as ds
from app import db
from app.sessions import Principal, current_user

router = APIRouter(prefix="/api/store", tags=["store"])


class CreateIn(BaseModel):
    kind: str = Field(pattern="^(nosql|kv)$")
    slug: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    visibility: str = Field(default="private", pattern="^(private|shared)$")
    versioning: str = Field(default="revisions", pattern="^(none|revisions|semver)$")
    sunset_days: int = Field(default=90, ge=0, le=3650)
    json_schema: dict | None = None
    default_ttl_seconds: int | None = Field(default=None, ge=1, le=31536000)


class SettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    visibility: str | None = Field(default=None, pattern="^(private|shared)$")
    versioning: str | None = Field(default=None, pattern="^(none|revisions|semver)$")
    sunset_days: int | None = Field(default=None, ge=0, le=3650)
    json_schema: dict | None = None
    default_ttl_seconds: int | None = Field(default=None, ge=1, le=31536000)


class ShareIn(BaseModel):
    group_id: int
    level: str = Field(pattern="^(read|write)$")


class SharesPut(BaseModel):
    shares: list[ShareIn] = Field(max_length=100)


class ItemIn(BaseModel):
    value: Any
    private: bool | None = None
    ttl_seconds: int | None = Field(default=None, ge=1, le=31536000)


class DocIn(ItemIn):
    key: str | None = Field(default=None, max_length=200)


class ReleaseIn(BaseModel):
    kind: str = Field(default="auto", pattern="^(auto|minor|major)$")
    note: str = Field(default="", max_length=2000)


def split_ref(ref: str, version: str | None) -> tuple[str, str | None]:
    """'kunder@3' → ('kunder', '3'); an explicit ?version= wins."""
    if "@" in ref:
        ref, pinned = ref.split("@", 1)
        return ref, version or pinned
    return ref, version


def _filter(raw: str | None) -> dict | None:
    if not raw:
        return None
    if len(raw) > 4000:
        raise HTTPException(400, "Filteret er for langt")
    try:
        value = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Filteret skal være gyldig JSON")
    if not isinstance(value, dict):
        raise HTTPException(400, "Filteret skal være et JSON-objekt, fx {\"status\": \"aktiv\"}")
    return value


async def summary(conn, c: dict, user: dict, level: str | None) -> dict:
    counts = await db.fetch_one(conn, """select count(*) as items from store_items where container_id = %s
                                         and (expires_at is null or expires_at > now())""", (c["id"],))
    pending = await db.fetch_one(conn, f"""select {ds.RELEASE_COLUMNS} from store_releases r
                                           where r.container_id = %s and r.status = 'pending'""", (c["id"],))
    return {
        **{k: c[k] for k in ("id", "kind", "slug", "name", "description", "group_id", "group_name", "visibility",
                             "versioning", "minor_rule", "major_rule", "sunset_days", "json_schema", "default_ttl_seconds",
                             "created_at", "updated_at", "archived_at")},
        "ref": f"~{c['slug']}" if c["owner_id"] is not None else c["slug"],
        "personal": c["owner_id"] is not None,
        "current_version": ds.version_label(c["current_major"], c["current_minor"]),
        "my_level": level,
        "items": counts["items"],
        "pending_release": ds.version_label(pending["major"], pending["minor"]) if pending else None,
    }


async def _open(conn, ref: str, user: Principal, need: str = "read") -> tuple[dict, str, dict]:
    c = await ds.resolve(conn, ref, user.user_id)
    row = await ds.user_row(conn, user.user_id)
    level = await ds.level_of(conn, c, row)
    if level is None:
        raise HTTPException(404, "Samlingen findes ikke")
    if ds.LEVELS.index(level) < ds.LEVELS.index(need):
        raise HTTPException(403, {"write": "Du kan kun læse i denne samling",
                                  "manage": "Kun ejeren kan ændre det"}[need])
    return c, level, row


# ── Containers ─────────────────────────────────────────────────────────────

@router.get("")
async def list_containers(user: Principal = Depends(current_user)):
    """Every container you can read: your own, your groups', and those shared with your groups."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select {ds.CONTAINER_COLUMNS} from store_containers c left join groups g on g.id = c.group_id
            where c.archived_at is null and (
                c.owner_id = %(u)s
                or exists (select 1 from store_raci r where r.container_id = c.id and r.user_id = %(u)s)
                or exists (select 1 from group_members m where m.group_id = c.group_id and m.user_id = %(u)s)
                or (c.visibility = 'shared' and exists (select 1 from store_shares s join group_members m
                        on m.group_id = s.group_id and m.user_id = %(u)s where s.container_id = c.id)))
            order by c.owner_id is null, lower(c.name)""", {"u": user.user_id})
        me = await ds.user_row(conn, user.user_id)
        out = []
        for c in rows:
            level = await ds.level_of(conn, c, me)
            if level:
                out.append(await summary(conn, c, me, level))
    return {"items": out}


@router.post("", status_code=201)
async def create_personal(body: CreateIn, user: Principal = Depends(current_user)):
    """A personal container, owned by you ("~slug"). Group containers are made in /admin/data/store."""
    async with db.connection() as conn:
        c = await ds.create(conn, kind=body.kind, slug=body.slug, name=body.name, description=body.description,
                            group_id=None, owner_id=user.user_id, visibility=body.visibility, versioning=body.versioning,
                            minor_rule="auto", major_rule="a_only", sunset_days=body.sunset_days,
                            json_schema=body.json_schema, default_ttl_seconds=body.default_ttl_seconds,
                            actor_id=user.user_id, raci=None, meta=user.meta)
        return await summary(conn, c, await ds.user_row(conn, user.user_id), "manage")


@router.get("/{ref}")
async def get_container(ref: str, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, level, me = await _open(conn, split_ref(ref, None)[0], user)
        out = await summary(conn, c, me, level)
        if c["owner_id"] is not None and level == "manage":
            out["shares"] = await ds.shares(conn, c["id"])
        return out


@router.patch("/{ref}")
async def update_personal(ref: str, body: SettingsIn, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, level, me = await _open(conn, ref, user, "manage")
        if c["owner_id"] is None:
            raise HTTPException(403, "En gruppes samling ændres i /admin/data/store")
        c = await ds.update_settings(conn, c, body.model_dump(exclude_unset=True), actor_id=user.user_id, meta=user.meta)
        return await summary(conn, c, me, level)


@router.put("/{ref}/shares")
async def put_personal_shares(ref: str, body: SharesPut, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, level, me = await _open(conn, ref, user, "manage")
        if c["owner_id"] is None:
            raise HTTPException(403, "En gruppes samling ændres i /admin/data/store")
        await ds.write_shares(conn, c["id"], [s.model_dump() for s in body.shares], user.user_id)
        await ds.audit(conn, "store.shares_changed", actor_id=user.user_id, meta=user.meta, table=ds.TABLE, row_id=c["id"],
                       shares=[s.model_dump() for s in body.shares])
        return {"shares": await ds.shares(conn, c["id"])}


@router.delete("/{ref}")
async def archive_personal(ref: str, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "manage")
        if c["owner_id"] is None:
            raise HTTPException(403, "En gruppes samling arkiveres i /admin/data/store")
        await ds.archive(conn, c, actor_id=user.user_id, meta=user.meta, reason=None)
    return {"ok": True}


# ── Items ──────────────────────────────────────────────────────────────────

@router.get("/{ref}/items")
async def list_items(ref: str, version: str | None = None, filter: str | None = None, prefix: str | None = None,
                     limit: int = 100, offset: int = 0, user: Principal = Depends(current_user)):
    ref, version = split_ref(ref, version)
    async with db.connection() as conn:
        c, level, _ = await _open(conn, ref, user)
        return await ds.list_items(conn, c, level=level, version=version, filter_=_filter(filter),
                                   prefix=prefix[:200] if prefix else None, limit=max(1, min(limit, 500)),
                                   offset=max(0, offset))


@router.post("/{ref}/items", status_code=201)
async def add_document(ref: str, body: DocIn, user: Principal = Depends(current_user)):
    """A new document with a generated key (or the key given, if it is free)."""
    import secrets as pysecrets
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "write")
        key = body.key or f"d_{pysecrets.token_hex(6)}"
        if await db.fetch_one(conn, "select 1 from store_items where container_id = %s and key = %s", (c["id"], key)):
            raise HTTPException(409, "Nøglen findes allerede")
        row = await ds.write_item(conn, c, key, body.value, private=body.private, ttl_seconds=body.ttl_seconds,
                                  actor_id=user.user_id, meta=user.meta)
        return ds.item_out(row)


@router.get("/{ref}/items/{key}")
async def get_item(ref: str, key: str, version: str | None = None, user: Principal = Depends(current_user)):
    ref, version = split_ref(ref, version)
    async with db.connection() as conn:
        c, level, _ = await _open(conn, ref, user)
        return await ds.get_item(conn, c, key, level=level, version=version)


@router.put("/{ref}/items/{key}")
async def put_item(ref: str, key: str, body: ItemIn, user: Principal = Depends(current_user)):
    if "@" in ref:
        raise HTTPException(400, "Kun arbejdskopien kan ændres — skriv uden @version")
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "write")
        row = await ds.write_item(conn, c, key, body.value, private=body.private, ttl_seconds=body.ttl_seconds,
                                  actor_id=user.user_id, meta=user.meta)
        return ds.item_out(row)


@router.delete("/{ref}/items/{key}")
async def delete_item(ref: str, key: str, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "write")
        await ds.delete_item(conn, c, key, actor_id=user.user_id, meta=user.meta)
    return {"ok": True}


@router.get("/{ref}/items/{key}/revisions")
async def item_revisions(ref: str, key: str, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "write")
        return {"items": await ds.revisions(conn, c, key)}


@router.post("/{ref}/items/{key}/revisions/{rev}/restore")
async def restore(ref: str, key: str, rev: int, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "write")
        return await ds.restore_revision(conn, c, key, rev, actor_id=user.user_id, meta=user.meta)


# ── Versions ───────────────────────────────────────────────────────────────

async def release_out(conn, rel: dict, me: dict) -> dict:
    approvals = await ds.release_approvals(conn, rel["id"])
    users = await ds.users_by_id(conn, [a["approver_id"] for a in approvals] + [rel["proposed_by"]])
    return {**rel, "version": f"{rel['major']}.{rel['minor']}", "proposed_by": ds.person(users.get(rel["proposed_by"])),
            "approvals": [{**{k: a[k] for k in ("roles", "decision", "note", "decided_at")},
                           "approver": ds.person(users.get(a["approver_id"]))} for a in approvals],
            "can_decide": await ds.can_decide(conn, rel, me), "own": rel["proposed_by"] == me["id"]}


@router.get("/{ref}/versions")
async def versions(ref: str, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, level, me = await _open(conn, ref, user)
        rows = await db.fetch_all(conn, f"""select {ds.RELEASE_COLUMNS} from store_releases r where r.container_id = %s
                                           order by r.id desc limit 100""", (c["id"],))
        writer = ds.LEVELS.index(level) >= ds.LEVELS.index("write")
        return {"current": ds.version_label(c["current_major"], c["current_minor"]),
                "items": [await release_out(conn, r, me) for r in rows if writer or r["status"] == "approved"],
                "next": await ds.analyse(conn, c) if writer and c["versioning"] == "semver" else None}


@router.post("/{ref}/versions", status_code=201)
async def propose(ref: str, body: ReleaseIn, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, _, me = await _open(conn, ref, user, "write")
        rel = await ds.propose_release(conn, c, kind=body.kind, note=body.note, actor_id=user.user_id, meta=user.meta)
        return await release_out(conn, rel, me)


@router.post("/{ref}/versions/{release_id}/cancel")
async def cancel(ref: str, release_id: int, user: Principal = Depends(current_user)):
    async with db.connection() as conn:
        c, _, _ = await _open(conn, ref, user, "write")
        rel = await ds.get_release(conn, release_id)
        if rel["container_id"] != c["id"]:
            raise HTTPException(404, "Versionen findes ikke")
        await ds.cancel(conn, release_id, user_id=user.user_id, meta=user.meta)
    return {"ok": True}
