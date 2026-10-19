"""Features and rings, after happy-groups member-features (FeaturesAdmin.tsx) without groups.

A feature is enabled for a user when feature.ring >= the user's level (feature_rings, default 4):

    ring 4 Stabilt   everyone            ring 2 Beta      users with level ≤ 2
    ring 3 Preview   everyone            ring 1 Internt   users with level 1

The app on / reads /api/features and records usage; /admin/features sets rings, observation and levels.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.features_catalog import CATALOG
from app.logs import audit, changed
from app.sessions import Principal, current_user, optional_principal, require_admin

router = APIRouter(prefix="/api/features", tags=["features"])
admin_router = APIRouter(prefix="/api/admin/features", tags=["admin"], dependencies=[Depends(require_admin)])

DEFAULT_LEVEL = 4
USAGE_DEDUPE_SECONDS = 60
AREAS = "^(bruger|integration|admin|platform)$"


async def user_level(conn, user_id: int | None) -> int:
    if user_id is None:
        return DEFAULT_LEVEL
    row = await db.fetch_one(conn, "select ring from feature_rings where user_id = %s", (user_id,))
    return row["ring"] if row else DEFAULT_LEVEL


# ── The app ─────────────────────────────────────────────────────────────────

@router.get("")
async def my_features(principal: Principal | None = Depends(optional_principal)):
    async with db.connection() as conn:
        level = await user_level(conn, principal.user_id if principal else None)
        rows = await db.fetch_all(conn, "select key, ring, observe from features order by key")
    return {"level": level, "features": [{**r, "enabled": r["ring"] >= level} for r in rows]}


class UsageIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=50)


@router.post("/usage")
async def record_usage(body: UsageIn, p: Principal = Depends(current_user)):
    """Batched from the app. At most one row per user and feature per minute; unknown keys are ignored."""
    async with db.connection() as conn:
        cur = await conn.execute(f"""
            insert into feature_usage (feature_key, user_id)
            select f.key, %s from features f
            where f.key = any(%s) and not exists (
                select 1 from feature_usage u where u.feature_key = f.key and u.user_id = %s
                  and u.ts > now() - interval '{USAGE_DEDUPE_SECONDS} seconds')""", (p.user_id, list(set(body.keys)), p.user_id))
    return {"recorded": cur.rowcount}


class LevelIn(BaseModel):
    ring: int | None = Field(default=None, ge=1, le=4)  # None: back to the default (ring 4)


async def _set_level(conn, user_id: int, ring: int | None, actor: Principal) -> None:
    before = await user_level(conn, user_id)
    if ring is None:
        await conn.execute("delete from feature_rings where user_id = %s", (user_id,))
    else:
        await conn.execute("""
            insert into feature_rings (user_id, ring, set_by) values (%s, %s, %s)
            on conflict (user_id) do update set ring = excluded.ring, set_by = excluded.set_by, updated_at = now()""",
            (user_id, ring, actor.user_id))
    await audit(conn, "feature.level", actor_id=actor.user_id, meta=actor.meta, table="feature_rings", row_id=user_id,
                before={"ring": before}, after={"ring": ring or DEFAULT_LEVEL})


@router.put("/level")
async def set_my_level(body: LevelIn, p: Principal = Depends(current_user)):
    """Konto → Dit niveau: the user chooses how early they want new features."""
    async with db.connection() as conn:
        await _set_level(conn, p.user_id, body.ring, p)
    return {"ok": True, "level": body.ring or DEFAULT_LEVEL}


# ── /admin/features ─────────────────────────────────────────────────────────

@admin_router.get("")
async def admin_features():
    async with db.connection() as conn:
        items = await db.fetch_all(conn, """
            select f.key, f.title, f.description, f.area, f.ring, f.observe, f.builtin,
                   count(u.id) as uses_30d, count(distinct u.user_id) as users_30d
            from features f left join feature_usage u on u.feature_key = f.key and u.ts > now() - interval '30 days'
            group by f.key order by f.builtin desc, f.created_at, f.key""")
        levels = await db.fetch_all(conn, """
            select r.user_id, r.ring, u.display_name, u.phone from feature_rings r join users u on u.id = r.user_id
            order by r.updated_at""")
    known = {i["key"] for i in items}
    missing = [{"key": c.key, "title": c.title, "description": c.description, "area": c.area}
               for c in CATALOG if c.key not in known]
    return {"items": items, "missing": missing, "levels": levels}


@admin_router.get("/{key}/usage")
async def admin_feature_usage(key: str):
    async with db.connection() as conn:
        return await db.fetch_all(conn, """
            select u.user_id, us.display_name, us.phone, count(*) as n, max(u.ts) as last
            from feature_usage u left join users us on us.id = u.user_id
            where u.feature_key = %s and u.ts > now() - interval '30 days'
            group by u.user_id, us.display_name, us.phone order by n desc limit 200""", (key,))


class FeatureIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=60)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    area: str = Field(pattern=AREAS)


@admin_router.post("", status_code=201)
async def create_feature(body: FeatureIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        if await db.fetch_one(conn, "select 1 from features where key = %s", (body.key,)):
            raise HTTPException(409, "Der findes allerede en feature med den nøgle")
        row = await db.fetch_one(conn, """
            insert into features (key, title, description, area, ring, created_by) values (%s, %s, %s, %s, 1, %s)
            returning *""", (body.key, body.title.strip(), body.description.strip(), body.area, admin.user_id))
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="features", row_id=body.key,
                    after={k: row[k] for k in ("title", "area", "ring")})
    return row


class FeaturePatch(BaseModel):
    ring: int | None = Field(default=None, ge=1, le=4)
    observe: bool | None = None


@admin_router.patch("/{key}")
async def update_feature(key: str, body: FeaturePatch, admin: Principal = Depends(require_admin)):
    """The only place a ring is set."""
    async with db.connection() as conn:
        before = await db.fetch_one(conn, "select ring, observe from features where key = %s for update", (key,))
        if before is None:
            raise HTTPException(404, "Featuren findes ikke")
        after = {"ring": before["ring"] if body.ring is None else body.ring,
                 "observe": before["observe"] if body.observe is None else body.observe}
        await conn.execute("update features set ring = %s, observe = %s, updated_at = now() where key = %s",
                           (after["ring"], after["observe"], key))
        b, a = changed(before, after)
        if a:
            await audit(conn, "feature.ring" if "ring" in a else "update", actor_id=admin.user_id, meta=admin.meta,
                        table="features", row_id=key, before=b, after=a)
    return {"key": key, **after}


@admin_router.delete("/{key}")
async def delete_feature(key: str, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select * from features where key = %s for update", (key,))
        if row is None:
            raise HTTPException(404, "Featuren findes ikke")
        if row["builtin"]:
            raise HTTPException(409, "Features fra koden kan ikke slettes")
        await conn.execute("delete from features where key = %s", (key,))
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="features", row_id=key,
                    before={k: row[k] for k in ("title", "area", "ring")})
    return {"ok": True}


@admin_router.post("/import")
async def import_catalog(admin: Principal = Depends(require_admin)):
    """Creates catalog features that are missing in the database, in ring 1. Existing rings are kept."""
    async with db.connection() as conn:
        created = []
        for c in CATALOG:
            cur = await conn.execute("""
                insert into features (key, title, description, area, ring, builtin, created_by)
                values (%s, %s, %s, %s, 1, true, %s) on conflict (key) do nothing""",
                (c.key, c.title, c.description, c.area, admin.user_id))
            if cur.rowcount:
                created.append(c.key)
        if created:
            await audit(conn, "feature.import", actor_id=admin.user_id, meta=admin.meta, table="features", created=created)
    return {"created": created}


@admin_router.put("/levels/{user_id}")
async def set_level(user_id: int, body: LevelIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        if not await db.fetch_one(conn, "select 1 from users where id = %s", (user_id,)):
            raise HTTPException(404, "Brugeren findes ikke")
        await _set_level(conn, user_id, body.ring, admin)
    return {"ok": True}


@admin_router.delete("/levels/{user_id}")
async def reset_level(user_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _set_level(conn, user_id, None, admin)
    return {"ok": True}
