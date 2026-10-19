"""Impersonation, after happy-groups admin/impersonation/routes.ts but without groups.

Admins create test accounts that cannot log in on their own (users.impersonation_only) and view the
app on / as one of them. Each test account belongs to the admin who created it
(impersonation_owner_id); nobody else sees or uses it. The impersonated session carries
impersonator_id and lives in its own cookie, so /admin keeps running as the real admin and every
change made as the test account is audited with the admin as actor.

    GET    /api/impersonation/status          the active impersonation, if any
    GET    /api/impersonation/users           your test accounts
    POST   /api/impersonation/users           create one {display_name, role}
    POST   /api/impersonation/users/presets   one per role (idempotent)
    DELETE /api/impersonation/users/{id}      remove a test account
    POST   /api/impersonation/start           {user_id} → the app on / runs as that account
    POST   /api/impersonation/stop            end it
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import config, db
from app.logs import audit
from app.security import new_token, token_hash
from app.sessions import IMP_COOKIE, Principal, require_admin

router = APIRouter(prefix="/api/impersonation", tags=["impersonation"], dependencies=[Depends(require_admin)])

SESSION_TTL = timedelta(hours=8)
EMAIL_DOMAIN = "impersonation.invalid"
ROLES = {"user": "Bruger", "admin": "Admin"}


class CreateIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    role: str = Field(pattern="^(user|admin)$")


class StartIn(BaseModel):
    user_id: int


def _dto(u: dict) -> dict:
    return {"id": u["id"], "display_name": u["display_name"], "role": u["role"], "handle": u["phone"],
            "created_at": u["created_at"]}


async def _owned(conn, user_id: int, owner_id: int) -> dict | None:
    return await db.fetch_one(conn, """
        select * from users where id = %s and impersonation_only and impersonation_owner_id = %s""", (user_id, owner_id))


async def _active(conn, request: Request, admin_id: int) -> dict | None:
    """The live impersonated session in this browser, with its test account."""
    token = request.cookies.get(IMP_COOKIE)
    if not token:
        return None
    return await db.fetch_one(conn, """
        select s.id as session_id, u.id, u.display_name, u.role, u.phone, u.created_at
        from sessions s join users u on u.id = s.user_id
        where s.token_hash = %s and s.impersonator_id = %s and s.revoked_at is null and s.expires_at > now()
          and u.impersonation_owner_id = %s""", (token_hash(token), admin_id, admin_id))


async def _create(conn, admin: Principal, display_name: str, role: str) -> dict:
    # phone is unique and required; a test account gets an address that can never be a phone number.
    user = await db.fetch_one(conn, """
        insert into users (phone, display_name, role, impersonation_only, impersonation_owner_id)
        values ('test-' || gen_random_uuid() || '@' || %s, %s, %s, true, %s) returning *""",
        (EMAIL_DOMAIN, display_name, role, admin.user_id))
    user = await db.fetch_one(conn, "update users set phone = %s where id = %s returning *",
                              (f"test-{user['id']}@{EMAIL_DOMAIN}", user["id"]))
    await audit(conn, "impersonation.user.create", actor_id=admin.user_id, meta=admin.meta, table="users",
                row_id=user["id"], after={"display_name": display_name, "role": role, "impersonation_only": True})
    return user


@router.get("/status")
async def status(request: Request, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        active = await _active(conn, request, admin.user_id)
    return {"user": _dto(active) if active else None}


@router.get("/users")
async def list_users(request: Request, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        users = await db.fetch_all(conn, """
            select * from users where impersonation_only and impersonation_owner_id = %s order by created_at, id""",
            (admin.user_id,))
        active = await _active(conn, request, admin.user_id)
    return {"users": [_dto(u) for u in users], "roles": [{"slug": k, "name": v} for k, v in ROLES.items()],
            "active_id": active["id"] if active else None}


@router.post("/users", status_code=201)
async def create_user(body: CreateIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        user = await _create(conn, admin, body.display_name.strip(), body.role)
    return _dto(user)


@router.post("/users/presets", status_code=201)
async def create_presets(admin: Principal = Depends(require_admin)):
    """„Én pr. rolle“: Test Bruger and Test Admin, if you do not already have one of that role."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select distinct role from users where impersonation_only and impersonation_owner_id = %s""", (admin.user_id,))
        have = {r["role"] for r in rows}
        created = [await _create(conn, admin, f"Test {name}", role) for role, name in ROLES.items() if role not in have]
    return {"created": [_dto(u) for u in created]}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        user = await _owned(conn, user_id, admin.user_id)
        if user is None:
            raise HTTPException(404, "Testkontoen findes ikke")
        active = await _active(conn, request, admin.user_id)
        if active and active["id"] == user_id:
            raise HTTPException(409, "Stop efterligningen af testkontoen først")
        await conn.execute("delete from users where id = %s", (user_id,))  # sessions cascade
        await audit(conn, "impersonation.user.delete", actor_id=admin.user_id, meta=admin.meta, table="users",
                    row_id=user_id, before={"display_name": user["display_name"], "role": user["role"]})
    return {"ok": True}


@router.post("/start")
async def start(body: StartIn, request: Request, response: Response, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        target = await _owned(conn, body.user_id, admin.user_id)
        if target is None:
            raise HTTPException(404, "Du kan kun efterligne dine egne testkonti")
        # Switching from one test account to another ends the previous impersonated session.
        previous = await _active(conn, request, admin.user_id)
        if previous:
            await conn.execute("update sessions set revoked_at = now() where id = %s", (previous["session_id"],))
        token = new_token()
        expires = datetime.now(timezone.utc) + SESSION_TTL
        await conn.execute("""
            insert into sessions (user_id, token_hash, scope, expires_at, ip, user_agent, impersonator_id)
            values (%s, %s, 'full', %s, %s, %s, %s)""",
            (target["id"], token_hash(token), expires, admin.meta.ip, admin.meta.user_agent, admin.user_id))
        await audit(conn, "impersonation.start", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=target["id"],
                    previous=previous["id"] if previous else None)
    response.set_cookie(IMP_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True,
                        secure=config.settings.secure_cookies, samesite="lax", path="/")
    return {"user": _dto(target), "impersonator": {"id": admin.user_id, "display_name": admin.display_name}}


@router.post("/stop")
async def stop(request: Request, response: Response, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        active = await _active(conn, request, admin.user_id)
        if active:
            await conn.execute("update sessions set revoked_at = now() where id = %s", (active["session_id"],))
            await audit(conn, "impersonation.stop", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=active["id"])
    response.delete_cookie(IMP_COOKIE, path="/")
    return {"ok": True, "user": {"id": admin.user_id, "display_name": admin.display_name}}
