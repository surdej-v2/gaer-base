"""/admin: only for users with the admin role. Every change is written to the audit log,
and every read is logged as admin.read by the require_admin dependency."""

import csv
import io
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app import admin_roles, config, db, gdpr, mail, mfa, og, retention, settings_store
from app.logs import audit, auth_event, changed
from app.routes.auth import parse_email, parse_phone
from app.routes.files import delete_file, save_upload
from app.routes.me import ProfileIn, avatar_response, clean_profile, update_profile_fields
from app.security import bootstrap_admin_email, bootstrap_admin_phone, mask_contact, normalize_email, normalize_phone, phone_hash
from app.sessions import Principal, require_admin, require_admin_stepup, revoke_sessions
from app.storage import get_storage

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 500)), max(0, offset)


# ── Users and roles ─────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(q: str | None = None, limit: int = 100, offset: int = 0, admin: Principal = Depends(require_admin)):
    limit, offset = _page(limit, offset)
    # Test accounts are only shown to the admin who owns them.
    where, params = "where (not u.impersonation_only or u.impersonation_owner_id = %s)", [admin.user_id]
    if q:
        where += " and (u.email ilike %s or u.phone ilike %s or coalesce(u.display_name, '') ilike %s)"
        params += [f"%{q.strip()}%"] * 3
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select u.id, u.email, u.phone, u.display_name, u.role, u.super_admin, u.admin_roles, u.grace_logins_used, u.blocked_at,
                   u.created_at, u.last_login_at, u.impersonation_only, u.mfa_required, u.audience,
                   (select ring from feature_rings r where r.user_id = u.id) as feature_ring,
                   (select count(*) from passkeys p where p.user_id = u.id) as passkeys,
                   (select count(*) from totp_factors t where t.user_id = u.id and t.confirmed_at is not null) as totp,
                   (select count(*) from sessions s where s.user_id = u.id and s.revoked_at is null
                        and s.expires_at > now()) as active_sessions,
                   (select coalesce(sum(size_bytes), 0) from files f where f.user_id = u.id) as file_bytes
            from users u {where} order by u.created_at limit %s offset %s""", params + [limit, offset])
        total = await db.fetch_one(conn, f"select count(*) as n from users u {where}", params)
    return {"items": rows, "total": total["n"], "grace_logins": config.settings.grace_logins,
            "admin_roles": admin_roles.catalog()}


class RoleIn(BaseModel):
    role: str = Field(pattern="^(user|admin)$")


async def _user_or_404(conn, user_id: int, admin: Principal | None = None) -> dict:
    """The user, locked. With admin: changing another admin's account needs the role 'fuld' (app/admin_roles.py),
    so a narrower role (e.g. Support) cannot block or reset someone with more rights than themselves."""
    user = await db.fetch_one(conn, "select * from users where id = %s for update", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    if (admin is not None and user["role"] == "admin" and user["id"] != admin.user_id
            and admin_roles.FULL not in admin.admin_roles and not admin.super_admin):
        raise HTTPException(403, "Kun admins med fuld adgang kan ændre andre admins")
    return user


async def _ensure_not_last_admin(conn, user: dict) -> None:
    if user["role"] == "admin" and not user["impersonation_only"]:
        n = await db.fetch_one(conn, """select count(*) as n from users
                                        where role = 'admin' and blocked_at is null and not impersonation_only""")
        if n["n"] <= 1:
            raise HTTPException(409, "Der skal være mindst én admin")


def _ensure_not_super_admin(user: dict) -> None:
    if user["super_admin"]:
        raise HTTPException(409, "Superadministratoren kan ikke mistes eller spærres")


# The user actions take a connection, so /api/admin and the MCP drafts (app/mcp/admin_server.py) share them,
# including the rules (super admin, last admin, who may reset whom).

async def do_set_role(conn, user: dict, role: str, admin: Principal) -> None:
    if user["role"] == role:
        return
    if role != "admin":
        _ensure_not_super_admin(user)
        await _ensure_not_last_admin(conn, user)
    await conn.execute("update users set role = %s, updated_at = now() where id = %s", (role, user["id"]))
    await audit(conn, "role.changed", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user["id"],
                before={"role": user["role"]}, after={"role": role})


async def do_block(conn, user: dict, admin: Principal) -> int:
    if user["id"] == admin.user_id:
        raise HTTPException(409, "Du kan ikke spærre dig selv")
    _ensure_not_super_admin(user)
    await _ensure_not_last_admin(conn, user)
    await conn.execute("update users set blocked_at = now(), updated_at = now() where id = %s", (user["id"],))
    await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user["id"],
                before={"blocked": False}, after={"blocked": True})
    return await revoke_sessions(conn, meta=admin.meta, user_id=user["id"], reason="blocked", actor_id=admin.user_id)


async def do_unblock(conn, user: dict, admin: Principal) -> None:
    await conn.execute("update users set blocked_at = null, updated_at = now() where id = %s", (user["id"],))
    await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user["id"],
                before={"blocked": True}, after={"blocked": False})


async def do_reset_passkeys(conn, user: dict, admin: Principal) -> int:
    """Deletes all passkeys and sessions. "Kræv passkey" is switched off if no other factor is left."""
    await _ensure_may_reset(user, admin)
    user_id = user["id"]
    rows = await db.fetch_all(conn, "delete from passkeys where user_id = %s returning id, name", (user_id,))
    await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="passkeys", row_id=None,
                before={"passkeys": rows}, subject_user_id=user_id, reason="admin.reset_passkeys")
    await auth_event(conn, "passkey.reset", ok=True, meta=admin.meta, user_id=user_id,
                     contact=user["email"] or user["phone"], count=len(rows), by=admin.user_id)
    if user["mfa_required"] and not (await mfa.factors(conn, user_id))["total"]:
        await conn.execute("update users set mfa_required = false, updated_at = now() where id = %s", (user_id,))
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                    before={"mfa_required": True}, after={"mfa_required": False}, reason="admin.reset_passkeys")
    await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason="passkeys_reset", actor_id=admin.user_id)
    return len(rows)


async def do_revoke_sessions(conn, user: dict, admin: Principal) -> int:
    """Ends every session of the user, including MCP clients (their tokens hang on the session)."""
    n = await revoke_sessions(conn, meta=admin.meta, user_id=user["id"], reason="admin", actor_id=admin.user_id)
    await audit(conn, "session.revoked", actor_id=admin.user_id, meta=admin.meta, table="sessions",
                subject_user_id=user["id"], count=n)
    return n


@router.patch("/users/{user_id}/role")
async def set_role(user_id: int, body: RoleIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        await do_set_role(conn, await _user_or_404(conn, user_id, admin), body.role, admin)
    return {"ok": True}


class AdminRolesIn(BaseModel):
    roles: list[str] = Field(min_length=1, max_length=len(admin_roles.ROLES))


@router.put("/users/{user_id}/admin-roles")
async def set_admin_roles(user_id: int, body: AdminRolesIn, admin: Principal = Depends(require_admin_stepup)):
    """Which parts of /admin an admin may use. Only admins with 'fuld' get here (app/admin_roles.py)."""
    roles = admin_roles.clean(body.roles)
    if len(roles) != len(set(body.roles)):
        raise HTTPException(422, "Ukendt admin-rolle")
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id, admin)
        if user["role"] != "admin" or user["impersonation_only"]:
            raise HTTPException(409, "Kun admins har admin-roller")
        if user["super_admin"] and admin_roles.FULL not in roles:
            raise HTTPException(409, "Superadministratoren har altid fuld adgang")
        before = sorted(user["admin_roles"] or [])
        if admin_roles.FULL in before and admin_roles.FULL not in roles:
            others = await db.fetch_one(conn, """
                select count(*) as n from users where role = 'admin' and 'fuld' = any (admin_roles) and blocked_at is null
                  and not impersonation_only and id <> %s""", (user_id,))
            if not others["n"]:
                raise HTTPException(409, "Der skal være mindst én admin med fuld adgang")
        if before != roles:
            await conn.execute("update users set admin_roles = %s, updated_at = now() where id = %s", (roles, user_id))
            await audit(conn, "role.changed", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                        before={"admin_roles": before}, after={"admin_roles": roles})
    return {"admin_roles": roles}


class AdminPhoneIn(BaseModel):
    phone: str = Field(max_length=32)


class AdminEmailIn(BaseModel):
    email: str = Field(max_length=254)


async def _set_contact(user_id: int, channel: str, contact: str, admin: Principal) -> dict:
    """Rectification by an admin, e.g. when a user lost access to the old address or number. The user's sessions
    are revoked and passkeys are kept; the next login with a code goes to the new contact."""
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id, admin)
        if user["impersonation_only"]:
            raise HTTPException(409, "En testkonto har ingen e-mail eller telefonnummer")
        if contact == user[channel]:
            return {"ok": True}
        if await db.fetch_one(conn, f"select 1 from users where {channel} = %s", (contact,)):
            raise HTTPException(409, "E-mailadressen bruges allerede af en anden bruger" if channel == "email"
                                else "Nummeret bruges allerede af en anden bruger")
        await conn.execute(f"update users set {channel} = %s, updated_at = now() where id = %s", (contact, user_id))
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                    before={channel: user[channel]}, after={channel: contact}, reason="rectification")
        await auth_event(conn, f"{channel}.changed", ok=True, meta=admin.meta, user_id=user_id, contact=contact,
                         by=admin.user_id)
        await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason=f"{channel}_changed",
                              actor_id=admin.user_id)
    return {"ok": True}


@router.patch("/users/{user_id}/email")
async def set_email(user_id: int, body: AdminEmailIn, admin: Principal = Depends(require_admin_stepup)):
    return await _set_contact(user_id, "email", parse_email(body.email), admin)


@router.patch("/users/{user_id}/phone")
async def set_phone(user_id: int, body: AdminPhoneIn, admin: Principal = Depends(require_admin_stepup)):
    return await _set_contact(user_id, "phone", parse_phone(body.phone), admin)


@router.post("/users/{user_id}/block")
async def block_user(user_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        await do_block(conn, await _user_or_404(conn, user_id, admin), admin)
    return {"ok": True}


@router.post("/users/{user_id}/unblock")
async def unblock_user(user_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        await do_unblock(conn, await _user_or_404(conn, user_id, admin), admin)
    return {"ok": True}


@router.post("/users/{user_id}/reset-passkeys")
async def reset_passkeys(user_id: int, admin: Principal = Depends(require_admin_stepup)):
    """Deletes all passkeys and sessions. The user logs in with a one-time code and can add a new passkey
    (grace logins are not given back). "Kræv passkey" is switched off if no other factor is left."""
    async with db.connection() as conn:
        n = await do_reset_passkeys(conn, await _user_or_404(conn, user_id, admin), admin)
    return {"ok": True, "deleted": n}


async def _ensure_may_reset(user: dict, admin: Principal) -> None:
    """Resetting another admin's factors could hand over that admin account, so only the super admin may do it.
    Your own factors are managed under /konto."""
    if user["id"] == admin.user_id:
        raise HTTPException(409, "Dine egne nøgler styrer du under Konto")
    if user["role"] == "admin" and not admin.super_admin:
        raise HTTPException(403, "Kun superadministratoren kan nulstille en anden administrators MFA")


@router.post("/users/{user_id}/reset-mfa")
async def reset_mfa(user_id: int, admin: Principal = Depends(require_admin_stepup)):
    """A user who lost every second factor: deletes passkeys, security keys and authenticator apps, switches
    "Kræv passkey" off and logs the user out everywhere. The next login is with a one-time code."""
    async with db.connection() as conn:
        deleted = await do_reset_mfa(conn, await _user_or_404(conn, user_id, admin), admin)
    return {"ok": True, "deleted": deleted}


async def do_reset_mfa(conn, user: dict, admin: Principal) -> dict:
    """Shared with the MCP draft user_reset_mfa."""
    await _ensure_may_reset(user, admin)
    user_id = user["id"]
    keys = await db.fetch_all(conn, "delete from passkeys where user_id = %s returning id, name, kind", (user_id,))
    apps = await db.fetch_all(conn, "delete from totp_factors where user_id = %s returning id, name", (user_id,))
    await conn.execute("update users set mfa_required = false, updated_at = now() where id = %s", (user_id,))
    await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                before={"passkeys": keys, "totp_factors": apps, "mfa_required": user["mfa_required"]},
                after={"mfa_required": False}, subject_user_id=user_id, reason="admin.reset_mfa")
    await auth_event(conn, "mfa.reset", ok=True, meta=admin.meta, user_id=user_id,
                     contact=user["email"] or user["phone"], keys=len(keys), apps=len(apps), by=admin.user_id)
    await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason="mfa_reset", actor_id=admin.user_id)
    return {"keys": len(keys), "apps": len(apps)}


@router.get("/users/{user_id}")
async def get_user(user_id: int, admin: Principal = Depends(require_admin)):
    """One user's profile and security status (factors, MFA, grace logins, sessions). Never secrets."""
    async with db.connection() as conn:
        user = await db.fetch_one(conn, """
            select id, email, phone, display_name, locale, time_zone, role, super_admin, admin_roles, mfa_required,
                   grace_logins_used, blocked_at, created_at, updated_at, last_login_at, impersonation_only,
                   impersonation_owner_id, avatar_file_id is not null as has_avatar
            from users where id = %s""", (user_id,))
        if user is None or (user["impersonation_only"] and user["impersonation_owner_id"] != admin.user_id):
            raise HTTPException(404, "Brugeren findes ikke")
        f = await mfa.factors(conn, user_id)
        keys = await db.fetch_all(conn, """select id, name, kind, created_at, last_used_at from passkeys
                                           where user_id = %s order by created_at""", (user_id,))
        apps = await db.fetch_all(conn, """select id, name, created_at, last_used_at from totp_factors
                                           where user_id = %s and confirmed_at is not null order by created_at""",
                                  (user_id,))
        sessions = await db.fetch_all(conn, """
            select id, created_at, last_seen_at, expires_at, host(ip) as ip, user_agent, mfa_at, grace from sessions
            where user_id = %s and scope = 'full' and revoked_at is null and expires_at > now()
            order by last_seen_at desc""", (user_id,))
    has_avatar = user.pop("has_avatar")
    s = config.settings
    return {
        "user": {**user, "avatar_url": f"/api/admin/users/{user_id}/avatar" if has_avatar else None},
        "security": {
            "factors": f,
            "methods": mfa.methods(f),
            "keys": keys,
            "totp": apps,
            "grace_logins": s.grace_logins,
            "grace_left": max(0, s.grace_logins - user["grace_logins_used"]) if user["role"] == "admin" and not f["total"] else None,
        },
        "sessions": sessions,
    }


@router.patch("/users/{user_id}/profile")
async def set_user_profile(user_id: int, body: ProfileIn, admin: Principal = Depends(require_admin)):
    """Rectification of name, language and time zone. E-mail and phone have their own endpoints."""
    values = clean_profile(body)
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id, admin)
        if user["impersonation_only"] and user["impersonation_owner_id"] != admin.user_id:
            raise HTTPException(404, "Brugeren findes ikke")
        await update_profile_fields(conn, user_id, values, actor_id=admin.user_id, meta=admin.meta)
    return {"ok": True}


@router.get("/users/{user_id}/avatar")
async def get_user_avatar(user_id: int, admin: Principal = Depends(require_admin)):
    return await avatar_response(user_id)


@router.post("/users/{user_id}/revoke-sessions")
async def admin_revoke_sessions(user_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        n = await do_revoke_sessions(conn, await _user_or_404(conn, user_id, admin), admin)
    return {"ok": True, "revoked": n}


# ── Logs ────────────────────────────────────────────────────────────────────

def _auth_filters(user_id, phone, device, ip, event, result, date_from, date_to):
    where, params = [], []
    if user_id:
        where.append("user_id = %s"); params.append(user_id)
    if phone:  # the full e-mail address or phone number
        try:
            contact = normalize_email(phone) if "@" in phone else normalize_phone(phone)
        except ValueError:
            raise HTTPException(400, "Ugyldig e-mailadresse eller telefonnummer")
        where.append("phone_hash = %s"); params.append(phone_hash(contact))
    if device:
        where.append("device ilike %s"); params.append(f"%{device}%")
    if ip:
        where.append("host(ip) like %s"); params.append(f"{ip}%")
    if event:
        where.append("event = %s"); params.append(event)
    if result:
        where.append("result = %s"); params.append(result)
    if date_from:
        where.append("ts >= %s"); params.append(date_from)
    if date_to:
        where.append("ts < %s"); params.append(date_to)
    return ("where " + " and ".join(where)) if where else "", params


@router.get("/logs/auth")
async def auth_log(user_id: int | None = None, phone: str | None = None, device: str | None = None,
                   ip: str | None = None, event: str | None = None, result: str | None = None,
                   date_from: datetime | None = Query(None, alias="from"),
                   date_to: datetime | None = Query(None, alias="to"), limit: int = 100, offset: int = 0):
    limit, offset = _page(limit, offset)
    where, params = _auth_filters(user_id, phone, device, ip, event, result, date_from, date_to)
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""
            select id, ts, event, result, user_id, phone_masked, host(ip) as ip, device, detail
            from auth_log {where} order by ts desc, id desc limit %s offset %s""", params + [limit, offset])
        total = await db.fetch_one(conn, f"select count(*) as n from auth_log {where}", params)
        events = await db.fetch_all(conn, "select distinct event from auth_log order by event")
    return {"items": items, "total": total["n"], "events": [e["event"] for e in events]}


def _audit_filters(actor_id, action, table, row_id, date_from, date_to):
    where, params = [], []
    if actor_id:
        where.append("actor_user_id = %s"); params.append(actor_id)
    if action:
        where.append("action = %s"); params.append(action)
    if table:
        where.append("table_name = %s"); params.append(table)
    if row_id:
        where.append("row_id = %s"); params.append(row_id)
    if date_from:
        where.append("ts >= %s"); params.append(date_from)
    if date_to:
        where.append("ts < %s"); params.append(date_to)
    return ("where " + " and ".join(where)) if where else "", params


@router.get("/logs/audit")
async def audit_log(actor_id: int | None = None, action: str | None = None, table: str | None = None,
                    row_id: str | None = None, date_from: datetime | None = Query(None, alias="from"),
                    date_to: datetime | None = Query(None, alias="to"), include_reads: bool = False,
                    limit: int = 100, offset: int = 0):
    limit, offset = _page(limit, offset)
    where, params = _audit_filters(actor_id, action, table, row_id, date_from, date_to)
    if not include_reads and not action:
        where = (where + " and " if where else "where ") + "action <> 'admin.read'"
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""
            select id, ts, actor_user_id, action, table_name, row_id, before, after, host(ip) as ip, detail
            from audit_log {where} order by ts desc, id desc limit %s offset %s""", params + [limit, offset])
        total = await db.fetch_one(conn, f"select count(*) as n from audit_log {where}", params)
        actions = await db.fetch_all(conn, "select distinct action from audit_log order by action")
        tables = await db.fetch_all(conn, "select distinct table_name from audit_log where table_name is not null order by 1")
    return {"items": items, "total": total["n"], "actions": [a["action"] for a in actions],
            "tables": [t["table_name"] for t in tables]}


@router.get("/logs/audit.csv")
async def audit_log_csv(actor_id: int | None = None, action: str | None = None, table: str | None = None,
                        row_id: str | None = None, date_from: datetime | None = Query(None, alias="from"),
                        date_to: datetime | None = Query(None, alias="to"),
                        admin: Principal = Depends(require_admin)):
    where, params = _audit_filters(actor_id, action, table, row_id, date_from, date_to)
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select id, ts, actor_user_id, action, table_name, row_id, before, after, host(ip) as ip, detail
            from audit_log {where} order by ts desc, id desc limit 100000""", params)
        await audit(conn, "log.exported", actor_id=admin.user_id, meta=admin.meta, log="audit_log",
                    rows=len(rows), filters={"actor_id": actor_id, "action": action, "table": table,
                                             "row_id": row_id, "from": date_from, "to": date_to})
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["id", "tidspunkt", "aktør", "handling", "tabel", "række", "før", "efter", "ip", "detaljer"])
    for r in rows:
        writer.writerow([r["id"], r["ts"].isoformat(), r["actor_user_id"] or "", r["action"], r["table_name"] or "",
                         r["row_id"] or "", json.dumps(r["before"], ensure_ascii=False) if r["before"] else "",
                         json.dumps(r["after"], ensure_ascii=False) if r["after"] else "", r["ip"] or "",
                         json.dumps(r["detail"], ensure_ascii=False)])
    filename = f"audit-log-{datetime.now():%Y%m%d-%H%M}.csv"
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Analytics and heatmaps ──────────────────────────────────────────────────

@router.get("/analytics")
async def analytics(days: int = 30):
    days = max(1, min(days, 400))
    since = "ts > now() - make_interval(days => %s)"
    async with db.connection() as conn:
        totals = await db.fetch_one(conn, f"""
            select count(*) filter (where kind = 'pageview') as pageviews,
                   count(distinct visitor_id) as visitors,
                   count(distinct session_id) as visits,
                   count(*) filter (where kind = 'pageview' and visitor_id is null) as anonymous_pageviews
            from analytics_events where {since}""", (days,))
        per_day = await db.fetch_all(conn, f"""
            select date_trunc('day', ts)::date as day, count(*) as pageviews,
                   count(distinct session_id) as visits
            from analytics_events where kind = 'pageview' and {since} group by 1 order by 1""", (days,))
        pages = await db.fetch_all(conn, f"""
            select path, count(*) as pageviews, count(distinct visitor_id) as visitors
            from analytics_events where kind = 'pageview' and {since}
            group by path order by pageviews desc limit 25""", (days,))
        sources = await db.fetch_all(conn, f"""
            select coalesce(referrer, 'Direkte') as source, count(*) as pageviews
            from analytics_events where kind = 'pageview' and {since}
              and (referrer is not null or prev_path is null)
            group by 1 order by 2 desc limit 15""", (days,))
        devices = await db.fetch_all(conn, f"""
            select device, count(*) as pageviews from analytics_events
            where kind = 'pageview' and {since} group by 1 order by 2 desc""", (days,))
        browsers = await db.fetch_all(conn, f"""
            select browser, count(*) as pageviews from analytics_events
            where kind = 'pageview' and {since} group by 1 order by 2 desc""", (days,))
        flows = await db.fetch_all(conn, f"""
            select prev_path as "from", path as "to", count(*) as n from analytics_events
            where kind = 'pageview' and prev_path is not null and prev_path <> path and {since}
            group by 1, 2 order by 3 desc limit 20""", (days,))
        entries = await db.fetch_all(conn, f"""
            select path, count(*) as visits from analytics_events
            where kind = 'pageview' and session_id is not null and prev_path is null and {since}
            group by 1 order by 2 desc limit 10""", (days,))
        consent = await db.fetch_one(conn, f"""
            select count(*) filter (where statistics) as accepted, count(*) filter (where not statistics) as declined
            from consents where {since}""", (days,))
        users, logins, logins_per_day = await _user_and_login_stats(conn, days)
        features = await db.fetch_all(conn, """
            select f.key, f.title, f.ring, count(*) as uses from feature_usage u join features f on f.key = u.feature_key
            where u.ts > now() - make_interval(days => %s) group by f.key order by uses desc limit 10""", (days,))
    return {"days": days, "totals": totals, "per_day": per_day, "pages": pages, "sources": sources,
            "devices": devices, "browsers": browsers, "flows": flows, "entries": entries, "consent": consent,
            "users": users, "logins": logins, "logins_per_day": logins_per_day, "features": features}


# A successful login is a passkey login or a login with a one-time code (grace login).
LOGIN_OK = "result = 'ok' and event in ('passkey.login', 'grace.used')"
LOGIN_FAILED = "result = 'fail' and event in ('passkey.login', 'otp.failed', 'otp.verified')"


async def _user_and_login_stats(conn, days: int) -> tuple[dict, dict, list[dict]]:
    """Key numbers for /admin → Statistik."""
    users = await db.fetch_one(conn, """
        select count(*) as total,
               count(*) filter (where u.created_at > now() - make_interval(days => %(d)s)) as new,
               count(*) filter (where u.last_login_at > now() - make_interval(days => %(d)s) and u.blocked_at is null) as active,
               count(*) filter (where exists (select 1 from passkeys p where p.user_id = u.id)) as with_passkey,
               count(*) filter (where not exists (select 1 from passkeys p where p.user_id = u.id)
                                  and u.grace_logins_used >= %(g)s) as must_passkey
        from users u where not u.impersonation_only""", {"d": days, "g": config.settings.grace_logins})
    logins = await db.fetch_one(conn, f"""
        select count(*) filter (where {LOGIN_OK} and event = 'passkey.login') as passkey,
               count(*) filter (where {LOGIN_OK} and event = 'grace.used') as otp,
               count(*) filter (where {LOGIN_FAILED}) as failed,
               count(*) filter (where {LOGIN_FAILED} and ts > now() - interval '1 day') as failed_24h
        from auth_log where ts > now() - make_interval(days => %s)""", (days,))
    per_day = await db.fetch_all(conn, f"""
        select date_trunc('day', ts)::date as day, count(*) as logins from auth_log
        where {LOGIN_OK} and ts > now() - make_interval(days => %s) group by 1 order by 1""", (days,))
    return users, logins, per_day


@router.get("/heatmaps/pages")
async def heatmap_pages(days: int = 30):
    async with db.connection() as conn:
        return await db.fetch_all(conn, """
            select path, count(*) filter (where kind = 'click') as clicks,
                   count(*) filter (where kind = 'scroll') as scrolls,
                   count(*) filter (where kind = 'pageview') as pageviews
            from analytics_events where ts > now() - make_interval(days => %s)
            group by path having count(*) filter (where kind in ('click', 'scroll')) > 0
            order by clicks desc limit 200""", (max(1, min(days, 400)),))


@router.get("/heatmaps")
async def heatmap(path: str, days: int = 30, device: str | None = None):
    days = max(1, min(days, 400))
    device_filter, params = ("and device = %s", [device]) if device else ("", [])
    async with db.connection() as conn:
        clicks = await db.fetch_all(conn, f"""
            select x, y from analytics_events
            where kind = 'click' and path = %s and ts > now() - make_interval(days => %s) {device_filter}
              and x is not null and y is not null
            order by ts desc limit 20000""", [path, days] + params)
        scroll = await db.fetch_all(conn, f"""
            select width_bucket(depth, 0, 1.0001, 10) as bucket, count(*) as n from analytics_events
            where kind = 'scroll' and path = %s and ts > now() - make_interval(days => %s) {device_filter}
            group by 1 order by 1""", [path, days] + params)
    # Share of visits that reached at least each 10 % step of the page.
    total = sum(r["n"] for r in scroll)
    reached, running = [], total
    by_bucket = {r["bucket"]: r["n"] for r in scroll}
    for b in range(1, 11):
        reached.append({"depth": (b - 1) * 10, "share": (running / total) if total else 0})
        running -= by_bucket.get(b, 0)
    return {"path": path, "clicks": [[c["x"], c["y"]] for c in clicks], "scroll": reached, "scroll_samples": total}


# ── GDPR requests ───────────────────────────────────────────────────────────

@router.get("/gdpr")
async def gdpr_list(status: str | None = None):
    where, params = ("where g.status = %s", [status]) if status else ("", [])
    async with db.connection() as conn:
        return await db.fetch_all(conn, f"""
            select g.*, coalesce(u.email, u.phone) as user_phone, u.display_name as user_name,
                   (g.status in ('received', 'in_progress') and g.due_at < now()) as overdue
            from gdpr_requests g left join users u on u.id = g.user_id {where}
            order by (g.status in ('received', 'in_progress')) desc, g.due_at""", params)


class GdprPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(received|in_progress|completed|rejected)$")
    notes: str | None = Field(default=None, max_length=8000)
    user_phone: str | None = None


async def _gdpr_or_404(conn, request_id: int) -> dict:
    row = await db.fetch_one(conn, "select * from gdpr_requests where id = %s for update", (request_id,))
    if row is None:
        raise HTTPException(404, "Anmodningen findes ikke")
    return row


@router.patch("/gdpr/{request_id}")
async def gdpr_update(request_id: int, body: GdprPatch, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await _gdpr_or_404(conn, request_id)
        after = {"status": body.status or before["status"], "notes": before["notes"] if body.notes is None else body.notes,
                 "user_id": before["user_id"]}
        if body.user_phone:  # e-mail address or phone number
            if "@" in body.user_phone:
                user = await db.fetch_one(conn, "select id from users where email = %s", (parse_email(body.user_phone),))
            else:
                user = await db.fetch_one(conn, "select id from users where phone = %s", (parse_phone(body.user_phone),))
            if user is None:
                raise HTTPException(404, "Ingen bruger med den e-mail eller det telefonnummer")
            after["user_id"] = user["id"]
        done = after["status"] in ("completed", "rejected")
        await conn.execute("""
            update gdpr_requests set status = %s, notes = %s, user_id = %s, handled_by = %s,
                   completed_at = case when %s then coalesce(completed_at, now()) end
            where id = %s""", (after["status"], after["notes"], after["user_id"], admin.user_id, done, request_id))
        b, a = changed(before, after)
        await audit(conn, "gdpr.request", actor_id=admin.user_id, meta=admin.meta, table="gdpr_requests",
                    row_id=request_id, before=b, after=a)
    return {"ok": True}


@router.get("/gdpr/{request_id}/export")
async def gdpr_export(request_id: int, admin: Principal = Depends(require_admin)):
    """Access request: one zip with data.json and copies of the user's files."""
    async with db.connection() as conn:
        req = await _gdpr_or_404(conn, request_id)
        if req["user_id"] is None:
            raise HTTPException(409, "Knyt anmodningen til en bruger først")
        body = await gdpr.export_zip(conn, req["user_id"])
        await audit(conn, "gdpr.export", actor_id=admin.user_id, meta=admin.meta, table="gdpr_requests",
                    row_id=request_id, subject_user_id=req["user_id"], bytes=len(body))
        if req["status"] == "received":
            await conn.execute("update gdpr_requests set status = 'in_progress', handled_by = %s where id = %s",
                               (admin.user_id, request_id))
    return Response(body, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{gdpr.export_filename(req["user_id"])}"'})


@router.post("/gdpr/{request_id}/erase")
async def gdpr_erase(request_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        req = await _gdpr_or_404(conn, request_id)
    if req["user_id"] is None:
        raise HTTPException(409, "Knyt anmodningen til en bruger først")
    if req["user_id"] == admin.user_id:
        raise HTTPException(409, "Slet din egen konto under Privatliv")
    result = await gdpr.erase_user(req["user_id"], actor_id=admin.user_id, meta=admin.meta,
                                   reason=f"gdpr.erasure #{request_id}", gdpr_request=request_id)
    return {"ok": True, **result}


# ── Retention ───────────────────────────────────────────────────────────────

class RetentionIn(BaseModel):
    auth_log_days: int = Field(ge=30, le=3650)
    audit_log_days: int = Field(ge=30, le=3650)
    analytics_days: int = Field(ge=7, le=3650)
    mcp_log_days: int = Field(default=90, ge=7, le=3650)


@router.get("/retention")
async def retention_get():
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
    return {
        "auth_log_days": values["retention_auth_log_days"],
        "audit_log_days": values["retention_audit_log_days"],
        "analytics_days": values["retention_analytics_days"],
        "mcp_log_days": values["retention_mcp_log_days"],
        "last_run": values["retention_last_run"],
        "logs": await retention.log_stats(),
    }


@router.put("/retention")
async def retention_put(body: RetentionIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        before = {k: values[f"retention_{k}"] for k in ("auth_log_days", "audit_log_days", "analytics_days",
                                                                 "mcp_log_days")}
        after = body.model_dump()
        await settings_store.set_many(conn, {f"retention_{k}": v for k, v in after.items()})
        b, a = changed(before, after)
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id="retention",
                    before=b, after=a)
    return {"ok": True}


@router.post("/retention/run")
async def retention_run(admin: Principal = Depends(require_admin)):
    return {"deleted": await retention.purge_old(actor_id=admin.user_id, meta=admin.meta, reason="manual")}


class PurgeUserIn(BaseModel):
    logs: list[str] = Field(min_length=1)


@router.post("/retention/users/{user_id}")
async def retention_purge_user(user_id: int, body: PurgeUserIn, admin: Principal = Depends(require_admin_stepup)):
    if not set(body.logs) <= set(retention.LOGS):
        raise HTTPException(400, f"Gyldige logs: {', '.join(retention.LOGS)}")
    return {"deleted": await retention.purge_user(user_id, logs=body.logs, actor_id=admin.user_id, meta=admin.meta,
                                                  reason="manual")}


# ── Consent and cookies ─────────────────────────────────────────────────────

class ConsentTextsIn(BaseModel):
    consent_title: str = Field(max_length=120)
    consent_text: str = Field(max_length=2000)
    consent_accept: str = Field(max_length=40)
    consent_decline: str = Field(max_length=40)
    ask_again: bool = False


@router.get("/consent")
async def consent_overview(limit: int = 100, offset: int = 0):
    limit, offset = _page(limit, offset)
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        # Latest answer per visitor (declines without id are counted individually).
        summary = await db.fetch_one(conn, """
            with latest as (
                select distinct on (coalesce(visitor_id, id::text)) statistics, version
                from consents order by coalesce(visitor_id, id::text), ts desc, id desc)
            select count(*) filter (where statistics) as accepted,
                   count(*) filter (where not statistics) as declined from latest""")
        records = await db.fetch_all(conn, """
            select c.id, c.ts, c.visitor_id, c.user_id, coalesce(u.email, u.phone) as user_phone, c.statistics, c.version, c.device
            from consents c left join users u on u.id = c.user_id
            order by c.ts desc limit %s offset %s""", (limit, offset))
        total = await db.fetch_one(conn, "select count(*) as n from consents")
    texts = {k: values[k] for k in ("consent_title", "consent_text", "consent_accept", "consent_decline",
                                    "consent_version")}
    return {"texts": texts, "summary": summary, "records": records, "total": total["n"]}


@router.put("/consent")
async def consent_texts(body: ConsentTextsIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        after = body.model_dump(exclude={"ask_again"})
        before = {k: values[k] for k in after}
        if body.ask_again:
            after["consent_version"] = int(values["consent_version"]) + 1
            before["consent_version"] = values["consent_version"]
        await settings_store.set_many(conn, after)
        b, a = changed(before, after)
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id="consent",
                    before=b, after=a)
    return {"ok": True}


# ── Files ───────────────────────────────────────────────────────────────────

@router.get("/files")
async def files_overview(user_id: int | None = None, limit: int = 200, offset: int = 0):
    limit, offset = _page(limit, offset)
    where, params = ("where f.user_id = %s", [user_id]) if user_id else ("", [])
    async with db.connection() as conn:
        totals = await db.fetch_one(conn, "select count(*) as files, coalesce(sum(size_bytes), 0) as bytes from files")
        per_user = await db.fetch_all(conn, """
            select f.user_id, coalesce(u.email, u.phone) as phone, u.display_name, count(*) as files, sum(f.size_bytes) as bytes
            from files f left join users u on u.id = f.user_id
            group by f.user_id, u.email, u.phone, u.display_name order by bytes desc limit 100""")
        items = await db.fetch_all(conn, f"""
            select f.id, f.user_id, coalesce(u.email, u.phone) as phone, f.filename, f.content_type, f.size_bytes, f.created_at
            from files f left join users u on u.id = f.user_id {where}
            order by f.created_at desc limit %s offset %s""", params + [limit, offset])
    return {"totals": totals, "per_user": per_user, "items": items}


@router.delete("/files/{file_id}")
async def files_delete(file_id: uuid.UUID, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await delete_file(conn, file_id, admin)
    return {"ok": True}


# ── Settings ────────────────────────────────────────────────────────────────

class SettingsIn(BaseModel):
    app_name: str = Field(min_length=1, max_length=80)
    sms_sender: str = Field(min_length=1, max_length=11, pattern=r"^[A-Za-z0-9 ]+$")
    gdpr_email: str = Field(default="", max_length=200)


@router.get("/settings")
async def settings_get():
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
    s = config.settings
    return {
        "app_name": values["app_name"], "sms_sender": values["sms_sender"], "gdpr_email": values["gdpr_email"],
        "logo_url": "/api/public/logo" if values["logo_key"] else None,
        "environment": {"version": s.version, "env": s.environment, "sms_provider": s.sms_provider,
                        "sms_login": s.sms_login, "mail_provider": s.mail_provider,
                        "mail_missing": mail.missing_config(),
                        "storage": s.storage_backend, "rp_id": s.rp_id, "grace_logins": s.grace_logins,
                        "bootstrap_admin": mask_contact(bootstrap_admin_email() or bootstrap_admin_phone())},
    }


@router.put("/settings")
async def settings_put(body: SettingsIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        after = body.model_dump()
        before = {k: values[k] for k in after}
        await settings_store.set_many(conn, after)
        b, a = changed(before, after)
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id="general",
                    before=b, after=a)
    return {"ok": True}


@router.post("/settings/logo")
async def settings_logo(file: UploadFile, admin: Principal = Depends(require_admin)):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Logoet skal være et billede")
    async with db.connection() as conn:
        old_key = await settings_store.get(conn, "logo_key")
        row = await save_upload(conn, file, owner_id=None, actor=admin, prefix="settings/logo")
        await settings_store.set_many(conn, {"logo_key": row["storage_key"]})
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id="logo",
                    before={"logo_key": old_key}, after={"logo_key": row["storage_key"]})
        if old_key:
            old = await db.fetch_one(conn, "select id from files where storage_key = %s", (old_key,))
            if old:
                await delete_file(conn, old["id"], admin)
    return {"ok": True, "logo_url": "/api/public/logo"}


# ── Website (/w) ────────────────────────────────────────────────────────────

class WebPageIn(BaseModel):
    slug: str = Field(default="", max_length=80, pattern="^[a-z0-9-]*$")
    lang: str = Field(pattern="^(da|en)$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=400)
    body: str = Field(default="", max_length=200_000)
    nav_order: int | None = None
    published: bool = False


class PreviewIn(BaseModel):
    body: str = Field(max_length=200_000)


@router.get("/web")
async def web_pages():
    async with db.connection() as conn:
        return await db.fetch_all(conn, "select * from web_pages order by slug, lang")


@router.post("/web/preview")
async def web_preview(body: PreviewIn):
    from app.routes.web import render_markdown
    return {"html": render_markdown(body.body)}


class WebFrontIn(BaseModel):
    front: str = Field(pattern="^(markdown|facit246)$")


@router.get("/web/front")
async def web_front_get():
    async with db.connection() as conn:
        return {"front": await settings_store.get(conn, "web_front")}


@router.put("/web/front")
async def web_front_put(body: WebFrontIn, admin: Principal = Depends(require_admin)):
    """/w/: the Markdown page with slug '' or the fixed "Facit · 246" story (app/web_front.py)."""
    async with db.connection() as conn:
        before = await settings_store.get(conn, "web_front")
        if before != body.front:
            await settings_store.set_many(conn, {"web_front": body.front})
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id="web_front",
                        before={"web_front": before}, after={"web_front": body.front})
    return {"ok": True, "front": body.front}


# ── Sharing images (og:image) for the anonymous pages, app/og.py ─────────────

async def _og_upload(conn, file: UploadFile, admin: Principal, prefix: str) -> str:
    head = await file.read(16)
    content_type = og.sniff(head)
    if content_type is None:
        raise HTTPException(400, "Billedet skal være PNG, JPEG eller WebP (helst 1200 × 630 pixels)")
    await file.seek(0)
    row = await save_upload(conn, file, owner_id=None, actor=admin, prefix=prefix, content_type=content_type)
    return row["storage_key"]


async def _og_forget(conn, key: str | None, admin: Principal) -> None:
    if key:
        old = await db.fetch_one(conn, "select id from files where storage_key = %s", (key,))
        if old:
            await delete_file(conn, old["id"], admin)


@router.get("/web/og")
async def web_og_list():
    async with db.connection() as conn:
        overrides = await settings_store.get(conn, "og_images")
    return {"surfaces": [{"name": name, "label": label, "custom": bool(overrides.get(name)),
                          "url": og.image_url(name, overrides)} for name, label in og.SURFACES.items()]}


@router.post("/web/og/{name}")
async def web_og_upload(name: str, file: UploadFile, admin: Principal = Depends(require_admin)):
    if name not in og.SURFACES:
        raise HTTPException(404, "Ukendt side")
    async with db.connection() as conn:
        overrides = dict(await settings_store.get(conn, "og_images"))
        old = overrides.get(name)
        overrides[name] = await _og_upload(conn, file, admin, "settings/og")
        await settings_store.set_many(conn, {"og_images": overrides})
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id=f"og_images.{name}",
                    before={"og_image": old}, after={"og_image": overrides[name]})
        await _og_forget(conn, old, admin)
    return {"ok": True, "url": og.image_url(name, overrides)}


@router.delete("/web/og/{name}")
async def web_og_reset(name: str, admin: Principal = Depends(require_admin)):
    """Back to the generated image."""
    async with db.connection() as conn:
        overrides = dict(await settings_store.get(conn, "og_images"))
        old = overrides.pop(name, None)
        if old:
            await settings_store.set_many(conn, {"og_images": overrides})
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings",
                        row_id=f"og_images.{name}", before={"og_image": old}, after={"og_image": None})
            await _og_forget(conn, old, admin)
    return {"ok": True, "url": og.image_url(name, overrides)}


@router.post("/web/{page_id}/og")
async def web_page_og_upload(page_id: int, file: UploadFile, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        page = await db.fetch_one(conn, "select id, og_image_key from web_pages where id = %s for update", (page_id,))
        if page is None:
            raise HTTPException(404, "Siden findes ikke")
        key = await _og_upload(conn, file, admin, "web/og")
        await conn.execute("update web_pages set og_image_key = %s, updated_at = now() where id = %s", (key, page_id))
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="web_pages", row_id=page_id,
                    before={"og_image_key": page["og_image_key"]}, after={"og_image_key": key})
        await _og_forget(conn, page["og_image_key"], admin)
    return {"ok": True, "og_image_key": key}


@router.delete("/web/{page_id}/og")
async def web_page_og_reset(page_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        page = await db.fetch_one(conn, "select id, og_image_key from web_pages where id = %s for update", (page_id,))
        if page is None:
            raise HTTPException(404, "Siden findes ikke")
        if page["og_image_key"]:
            await conn.execute("update web_pages set og_image_key = null, updated_at = now() where id = %s", (page_id,))
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="web_pages", row_id=page_id,
                        before={"og_image_key": page["og_image_key"]}, after={"og_image_key": None})
            await _og_forget(conn, page["og_image_key"], admin)
    return {"ok": True}


@router.post("/web")
async def web_create(body: WebPageIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        exists = await db.fetch_one(conn, "select 1 from web_pages where slug = %s and lang = %s", (body.slug, body.lang))
        if exists:
            raise HTTPException(409, "Der findes allerede en side med den adresse og det sprog")
        row = await db.fetch_one(conn, """
            insert into web_pages (slug, lang, title, description, body, nav_order, published)
            values (%(slug)s, %(lang)s, %(title)s, %(description)s, %(body)s, %(nav_order)s, %(published)s)
            returning *""", body.model_dump())
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="web_pages", row_id=row["id"],
                    after={k: v for k, v in body.model_dump().items() if k != "body"})
    return row


@router.put("/web/{page_id}")
async def web_update(page_id: int, body: WebPageIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await db.fetch_one(conn, "select * from web_pages where id = %s for update", (page_id,))
        if before is None:
            raise HTTPException(404, "Siden findes ikke")
        clash = await db.fetch_one(conn, "select 1 from web_pages where slug = %s and lang = %s and id <> %s",
                                   (body.slug, body.lang, page_id))
        if clash:
            raise HTTPException(409, "Der findes allerede en side med den adresse og det sprog")
        after = body.model_dump()
        await conn.execute("""
            update web_pages set slug = %(slug)s, lang = %(lang)s, title = %(title)s, description = %(description)s,
                   body = %(body)s, nav_order = %(nav_order)s, published = %(published)s, updated_at = now()
            where id = %(id)s""", {**after, "id": page_id})
        b, a = changed(before, after)
        if "body" in a:  # keep the log readable: note that the text changed, not the whole text
            b["body"], a["body"] = f"{len(before['body'])} tegn", f"{len(body.body)} tegn"
        if a:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="web_pages", row_id=page_id,
                        before=b, after=a)
    return {"ok": True}


@router.delete("/web/{page_id}")
async def web_delete(page_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from web_pages where id = %s returning slug, lang, title, og_image_key",
                                 (page_id,))
        if row is None:
            raise HTTPException(404, "Siden findes ikke")
        await _og_forget(conn, row.pop("og_image_key"), admin)
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="web_pages", row_id=page_id,
                    before=row)
    return {"ok": True}


# ── AI ──────────────────────────────────────────────────────────────────────

class AiSettingsIn(BaseModel):
    default_monthly_tokens: int = Field(ge=0, le=100_000_000)


class AiUserLimitIn(BaseModel):
    monthly_tokens: int | None = Field(default=None, ge=0, le=100_000_000)


@router.get("/ai")
async def ai_overview():
    from app import ai
    s = config.settings
    async with db.connection() as conn:
        default = await settings_store.get(conn, "ai_default_monthly_tokens")
        users = await db.fetch_all(conn, """
            select u.id, coalesce(u.email, u.phone) as phone, u.display_name, u.ai_monthly_tokens,
                   coalesce(sum(a.prompt_tokens + a.completion_tokens), 0) as used,
                   count(a.id) as requests
            from users u left join ai_usage a on a.user_id = u.id and a.ts >= date_trunc('month', now())
            group by u.id order by used desc, u.id""")
        per_surface = await db.fetch_all(conn, """
            select surface, count(*) as requests, sum(prompt_tokens + completion_tokens) as tokens
            from ai_usage where ts >= date_trunc('month', now()) group by surface order by tokens desc""")
        per_day = await db.fetch_all(conn, """
            select date_trunc('day', ts)::date as day, sum(prompt_tokens + completion_tokens) as tokens
            from ai_usage where ts > now() - interval '30 days' group by 1 order by 1""")
    return {"enabled": ai.enabled(), "base_url": s.ai_base_url, "model": s.ai_model,
            "default_monthly_tokens": default, "users": users, "per_surface": per_surface, "per_day": per_day}


@router.put("/ai")
async def ai_settings(body: AiSettingsIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await settings_store.get(conn, "ai_default_monthly_tokens")
        await settings_store.set_many(conn, {"ai_default_monthly_tokens": body.default_monthly_tokens})
        if before != body.default_monthly_tokens:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="settings", row_id="ai",
                        before={"default_monthly_tokens": before}, after={"default_monthly_tokens": body.default_monthly_tokens})
    return {"ok": True}


@router.put("/ai/users/{user_id}")
async def ai_user_limit(user_id: int, body: AiUserLimitIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id)
        await conn.execute("update users set ai_monthly_tokens = %s, updated_at = now() where id = %s",
                           (body.monthly_tokens, user_id))
        if user["ai_monthly_tokens"] != body.monthly_tokens:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                        before={"ai_monthly_tokens": user["ai_monthly_tokens"]},
                        after={"ai_monthly_tokens": body.monthly_tokens})
    return {"ok": True}
