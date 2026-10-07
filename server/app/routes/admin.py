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

from app import config, db, gdpr, retention, settings_store
from app.logs import audit, auth_event, changed
from app.routes.files import delete_file, save_upload
from app.security import bootstrap_admin_phone, mask_phone, normalize_phone, phone_hash
from app.sessions import Principal, require_admin, revoke_sessions
from app.storage import get_storage

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 500)), max(0, offset)


# ── Users and roles ─────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(q: str | None = None, limit: int = 100, offset: int = 0):
    limit, offset = _page(limit, offset)
    where, params = "", []
    if q:
        where = "where u.phone ilike %s or coalesce(u.display_name, '') ilike %s"
        params = [f"%{q.strip()}%", f"%{q.strip()}%"]
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select u.id, u.phone, u.display_name, u.role, u.grace_logins_used, u.blocked_at, u.created_at,
                   u.last_login_at,
                   (select count(*) from passkeys p where p.user_id = u.id) as passkeys,
                   (select count(*) from sessions s where s.user_id = u.id and s.revoked_at is null
                        and s.expires_at > now()) as active_sessions,
                   (select coalesce(sum(size_bytes), 0) from files f where f.user_id = u.id) as file_bytes
            from users u {where} order by u.created_at limit %s offset %s""", params + [limit, offset])
        total = await db.fetch_one(conn, f"select count(*) as n from users u {where}", params)
    return {"items": rows, "total": total["n"], "grace_logins": config.settings.grace_logins}


class RoleIn(BaseModel):
    role: str = Field(pattern="^(user|admin)$")


async def _user_or_404(conn, user_id: int) -> dict:
    user = await db.fetch_one(conn, "select * from users where id = %s for update", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    return user


async def _ensure_not_last_admin(conn, user: dict) -> None:
    if user["role"] == "admin":
        n = await db.fetch_one(conn, "select count(*) as n from users where role = 'admin' and blocked_at is null")
        if n["n"] <= 1:
            raise HTTPException(409, "Der skal være mindst én admin")


@router.patch("/users/{user_id}/role")
async def set_role(user_id: int, body: RoleIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id)
        if user["role"] == body.role:
            return {"ok": True}
        if body.role != "admin":
            await _ensure_not_last_admin(conn, user)
        await conn.execute("update users set role = %s, updated_at = now() where id = %s", (body.role, user_id))
        await audit(conn, "role.changed", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                    before={"role": user["role"]}, after={"role": body.role})
    return {"ok": True}


class AdminPhoneIn(BaseModel):
    phone: str = Field(max_length=32)


@router.patch("/users/{user_id}/phone")
async def set_phone(user_id: int, body: AdminPhoneIn, admin: Principal = Depends(require_admin)):
    """Rectification by an admin, e.g. when a user lost the old number. The user's sessions are revoked
    and passkeys are kept; the next login with a code goes to the new number."""
    try:
        phone = normalize_phone(body.phone)
    except ValueError:
        raise HTTPException(400, "Ugyldigt telefonnummer")
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id)
        if phone == user["phone"]:
            return {"ok": True}
        if await db.fetch_one(conn, "select 1 from users where phone = %s", (phone,)):
            raise HTTPException(409, "Nummeret bruges allerede af en anden bruger")
        await conn.execute("update users set phone = %s, updated_at = now() where id = %s", (phone, user_id))
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                    before={"phone": user["phone"]}, after={"phone": phone}, reason="rectification")
        await auth_event(conn, "phone.changed", ok=True, meta=admin.meta, user_id=user_id, phone=phone,
                         by=admin.user_id)
        await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason="phone_changed", actor_id=admin.user_id)
    return {"ok": True}


@router.post("/users/{user_id}/block")
async def block_user(user_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id)
        if user_id == admin.user_id:
            raise HTTPException(409, "Du kan ikke spærre dig selv")
        await _ensure_not_last_admin(conn, user)
        await conn.execute("update users set blocked_at = now(), updated_at = now() where id = %s", (user_id,))
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                    before={"blocked": False}, after={"blocked": True})
        await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason="blocked", actor_id=admin.user_id)
    return {"ok": True}


@router.post("/users/{user_id}/unblock")
async def unblock_user(user_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _user_or_404(conn, user_id)
        await conn.execute("update users set blocked_at = null, updated_at = now() where id = %s", (user_id,))
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="users", row_id=user_id,
                    before={"blocked": True}, after={"blocked": False})
    return {"ok": True}


@router.post("/users/{user_id}/reset-passkeys")
async def reset_passkeys(user_id: int, admin: Principal = Depends(require_admin)):
    """Deletes all passkeys and sessions. The user logs in with a one-time code and must
    register a new passkey (grace logins are not given back)."""
    async with db.connection() as conn:
        user = await _user_or_404(conn, user_id)
        rows = await db.fetch_all(conn, "delete from passkeys where user_id = %s returning id, name", (user_id,))
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="passkeys", row_id=None,
                    before={"passkeys": rows}, subject_user_id=user_id, reason="admin.reset_passkeys")
        await auth_event(conn, "passkey.reset", ok=True, meta=admin.meta, user_id=user_id, phone=user["phone"],
                         count=len(rows), by=admin.user_id)
        await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason="passkeys_reset",
                              actor_id=admin.user_id)
    return {"ok": True, "deleted": len(rows)}


@router.post("/users/{user_id}/revoke-sessions")
async def admin_revoke_sessions(user_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _user_or_404(conn, user_id)
        n = await revoke_sessions(conn, meta=admin.meta, user_id=user_id, reason="admin", actor_id=admin.user_id)
        await audit(conn, "session.revoked", actor_id=admin.user_id, meta=admin.meta, table="sessions",
                    subject_user_id=user_id, count=n)
    return {"ok": True, "revoked": n}


# ── Logs ────────────────────────────────────────────────────────────────────

def _auth_filters(user_id, phone, device, ip, event, result, date_from, date_to):
    where, params = [], []
    if user_id:
        where.append("user_id = %s"); params.append(user_id)
    if phone:
        try:
            where.append("phone_hash = %s"); params.append(phone_hash(normalize_phone(phone)))
        except ValueError:
            raise HTTPException(400, "Ugyldigt telefonnummer")
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
    return {"days": days, "totals": totals, "per_day": per_day, "pages": pages, "sources": sources,
            "devices": devices, "browsers": browsers, "flows": flows, "entries": entries, "consent": consent}


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
            select g.*, u.phone as user_phone, u.display_name as user_name,
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
        if body.user_phone:
            user = await db.fetch_one(conn, "select id from users where phone = %s", (normalize_phone(body.user_phone),))
            if user is None:
                raise HTTPException(404, "Ingen bruger med det telefonnummer")
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
async def gdpr_erase(request_id: int, admin: Principal = Depends(require_admin)):
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


@router.get("/retention")
async def retention_get():
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
    return {
        "auth_log_days": values["retention_auth_log_days"],
        "audit_log_days": values["retention_audit_log_days"],
        "analytics_days": values["retention_analytics_days"],
        "last_run": values["retention_last_run"],
        "logs": await retention.log_stats(),
    }


@router.put("/retention")
async def retention_put(body: RetentionIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        before = {k: values[f"retention_{k}"] for k in ("auth_log_days", "audit_log_days", "analytics_days")}
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
async def retention_purge_user(user_id: int, body: PurgeUserIn, admin: Principal = Depends(require_admin)):
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
            select c.id, c.ts, c.visitor_id, c.user_id, u.phone as user_phone, c.statistics, c.version, c.device
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
            select f.user_id, u.phone, u.display_name, count(*) as files, sum(f.size_bytes) as bytes
            from files f left join users u on u.id = f.user_id
            group by f.user_id, u.phone, u.display_name order by bytes desc limit 100""")
        items = await db.fetch_all(conn, f"""
            select f.id, f.user_id, u.phone, f.filename, f.content_type, f.size_bytes, f.created_at
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
                        "storage": s.storage_backend, "rp_id": s.rp_id, "grace_logins": s.grace_logins,
                        "bootstrap_admin": mask_phone(bootstrap_admin_phone())},
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
        row = await db.fetch_one(conn, "delete from web_pages where id = %s returning slug, lang, title", (page_id,))
        if row is None:
            raise HTTPException(404, "Siden findes ikke")
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
            select u.id, u.phone, u.display_name, u.ai_monthly_tokens,
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
