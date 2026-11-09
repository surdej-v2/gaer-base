"""/api/admin/mail: the operator console for mail, calendars and contacts (/admin/mail) and the admin view of one
user's mail (/admin/mail/users/<id>). Section "/mail" in app/admin_roles.py; every read is an admin.read line and
every change a mail.* entry in the audit log.

Admins manage mailboxes and collections, never their content. Destructive actions (suspend, delete, revoking all app
passwords, rotating the relay credentials) need a recent second factor and a reason of at least a few words.
"""

import asyncio
import csv
import io
import json
import secrets
import ssl
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import config, db, jobs, settings_store, webhooks
from app.logs import audit
from app.mailbox import backend as mb_backend
from app.mailbox import dns as mail_dns
from app.mailbox import service
from app.mailbox.backend import ADDRESS_BOOK, CALENDAR, MailBackendError
from app.routes.mail import activity, availability, overview, shared_out
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/mail", tags=["admin"], dependencies=[Depends(require_admin)])

QUEUE_WARN_MINUTES = 30
BOUNCE_WARN, COMPLAINT_WARN = 0.05, 0.001
CERT_WARN_DAYS = 14


def _on() -> None:
    service.require_enabled()


async def _user(conn, user_id: int) -> dict:
    user = await db.fetch_one(conn, """select id, email, display_name, role, blocked_at, created_at, last_login_at
                                       from users where id = %s and not impersonation_only""", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    return user


# ── Health helpers (also used by Overblik for warnings) ─────────────────────

_cert_cache: dict = {}


async def cert_expiry(host: str | None) -> dict:
    """notAfter of the certificate on IMAPS (993), cached for 10 minutes."""
    if not host:
        return {"host": None, "not_after": None, "days_left": None, "error": "MAIL_HOSTNAME er ikke sat"}
    cached = _cert_cache.get(host)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    out = {"host": host, "not_after": None, "days_left": None, "error": None}
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, 993, ssl=ssl.create_default_context(),
                                                                   server_hostname=host), 5)
        cert = writer.get_extra_info("peercert")
        writer.close()
        not_after = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
        out.update(not_after=not_after, days_left=(not_after - datetime.now(timezone.utc)).days)
    except Exception as e:  # unreachable, wrong name, expired …: shown as a warning
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    _cert_cache[host] = (time.monotonic() + 600, out)
    return out


async def rates(conn, hours: int = 24) -> dict:
    row = await db.fetch_one(conn, """
        select count(distinct coalesce(message_id, id::text)) filter (where type in ('sent', 'delivered', 'bounced', 'complained')) as messages,
               count(*) filter (where type = 'bounced') as bounced, count(*) filter (where type = 'complained') as complained,
               count(*) filter (where type = 'delivered') as delivered
        from mail_events where at > now() - make_interval(hours => %s)""", (hours,))
    n = row["messages"] or 0
    return {**row, "bounce_rate": (row["bounced"] / n) if n else 0.0, "complaint_rate": (row["complained"] / n) if n else 0.0}


async def warnings(conn, health: dict | None = None, cert: dict | None = None) -> list[dict]:
    out = []
    r = await rates(conn)
    if r["bounce_rate"] > BOUNCE_WARN:
        out.append({"key": "bounce", "tab": "sending", "text": f"Bounce-rate {r['bounce_rate']:.1%} de sidste 24 timer"})
    if r["complaint_rate"] > COMPLAINT_WARN:
        out.append({"key": "complaints", "tab": "sending",
                    "text": f"Klager {r['complaint_rate']:.2%} de sidste 24 timer"})
    for d in await db.fetch_all(conn, "select name, checks from mail_domains where status <> 'suspended'"):
        bad = [k.upper() for k in ("dkim", "dmarc") if d["checks"] and not (d["checks"].get(k) or {}).get("ok", False)]
        if bad and d["checks"]:
            out.append({"key": f"domain:{d['name']}", "tab": "domains", "text": f"{d['name']} fejler {' og '.join(bad)}"})
    last = await service.last_retention_run(conn)
    if last is None or (last["finished_at"] or last["started_at"]) < datetime.now(timezone.utc) - timedelta(hours=36):
        out.append({"key": "retention", "tab": "retention", "text": "Sletning af gammel mail har ikke kørt det sidste døgn"})
    stuck = await db.fetch_one(conn, """select count(*) as n from mailboxes where status = 'provisioning'
                                        and requested_at < now() - interval '15 minutes'""")
    if stuck["n"]:
        out.append({"key": "provisioning", "tab": "mailboxes", "text": f"{stuck['n']} postkasse(r) venter på at blive sat op"})
    drift = await db.fetch_one(conn, "select count(*) as n from mailboxes where drift is not null and status <> 'deleted'")
    if drift["n"]:
        out.append({"key": "drift", "tab": "health", "text": f"{drift['n']} postkasse(r) afviger mellem appen og Stalwart"})
    queue = (health or {}).get("queue") or {}
    oldest = queue.get("oldest_at")
    if oldest:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
            if age > timedelta(minutes=QUEUE_WARN_MINUTES):
                out.append({"key": "queue", "tab": "health",
                            "text": f"Den ældste mail i udgående kø har ventet {int(age.total_seconds() // 60)} min."})
        except ValueError:
            pass
    if health is not None and not health.get("reachable"):
        out.append({"key": "stalwart", "tab": "health", "text": "Stalwart svarer ikke"})
    if cert and cert.get("days_left") is not None and cert["days_left"] < CERT_WARN_DAYS:
        out.append({"key": "cert", "tab": "health", "text": f"TLS-certifikatet udløber om {cert['days_left']} dage"})
    return out


async def _health() -> dict:
    try:
        return await mb_backend.get_backend().health()
    except MailBackendError as e:
        return {"reachable": False, "error": str(e)}


# ── Overblik ────────────────────────────────────────────────────────────────

@router.get("/overview")
async def get_overview():
    if not mb_backend.enabled():
        return {"enabled": False, "backend": config.settings.mailbox_backend}
    health, cert = await asyncio.gather(_health(), cert_expiry(config.settings.mail_hostname))
    async with db.connection() as conn:
        counts = await db.fetch_one(conn, """
            select count(*) filter (where kind = 'personal' and status <> 'deleted') as personal,
                   count(*) filter (where kind = 'shared' and status <> 'deleted') as shared,
                   count(*) filter (where status = 'suspended') as suspended,
                   count(*) filter (where status = 'provisioning') as provisioning,
                   coalesce(sum(used_bytes) filter (where status <> 'deleted'), 0) as used_bytes,
                   coalesce(sum(quota_mb::bigint * 1048576) filter (where status <> 'deleted'), 0) as quota_bytes
            from mailboxes""")
        per_day = await db.fetch_all(conn, """
            select date_trunc('day', at)::date as day,
                   count(distinct coalesce(message_id, id::text)) filter (where type in ('sent', 'delivered')) as sent,
                   count(*) filter (where type = 'bounced') as bounced,
                   coalesce(sum(count) filter (where type = 'expired'), 0) as expired
            from mail_events where at > now() - interval '14 days' group by 1 order by 1""")
        month = await db.fetch_one(conn, """select count(distinct coalesce(message_id, id::text)) as n from mail_events
                                            where type in ('sent', 'delivered', 'bounced', 'complained')
                                              and at >= date_trunc('month', now())""")
        return {"enabled": True, "backend": config.settings.mailbox_backend, "counts": counts, "per_day": per_day,
                "lettermint_month": {"messages": month["n"], "quota": config.settings.lettermint_monthly_quota or None},
                "collections": await db.fetch_one(conn, """select count(*) filter (where type = 'calendar') as calendars,
                                                                   count(*) filter (where type = 'address_book') as address_books
                                                            from mail_collections"""),
                "last_retention": await service.last_retention_run(conn),
                "warnings": await warnings(conn, health, cert), "health": {"reachable": health.get("reachable")}}


# ── Domæner ─────────────────────────────────────────────────────────────────

class DomainIn(BaseModel):
    name: str = Field(min_length=4, max_length=253)
    dkim_selector: str = Field(default="lm", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")


class DomainPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(pending|suspended)$")
    dkim_selector: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    lettermint_domain_id: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


def _domain_out(d: dict) -> dict:
    return {**d, "records": mail_dns.required_records(d["name"], d["dkim_selector"])}


@router.get("/domains")
async def list_domains():
    _on()
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select d.*, (select count(*) from mailboxes m where m.domain_id = d.id
                                                         and m.status <> 'deleted') as mailboxes
                                           from mail_domains d order by d.name""")
    return [_domain_out(d) for d in rows]


@router.post("/domains")
async def add_domain(body: DomainIn, p: Principal = Depends(require_admin)):
    _on()
    name = body.name.strip().lower().rstrip(".")
    async with db.connection() as conn:
        if await db.fetch_one(conn, "select 1 from mail_domains where name = %s", (name,)):
            raise HTTPException(409, "Domænet findes allerede")
        try:
            row = await db.fetch_one(conn, """insert into mail_domains (name, dkim_selector, created_by)
                                              values (%s, %s, %s) returning *""", (name, body.dkim_selector, p.user_id))
        except Exception:
            raise HTTPException(400, "Ugyldigt domænenavn")
        await audit(conn, "mail.domain.create", actor_id=p.user_id, meta=p.meta, table="mail_domains", row_id=row["id"],
                    after={"name": name, "dkim_selector": body.dkim_selector})
    return _domain_out({**row, "mailboxes": 0})


@router.post("/domains/{domain_id}/verify")
async def verify_domain(domain_id: int, p: Principal = Depends(require_admin)):
    """Checks the records live. A domain is verified when MX, SPF, DKIM and DMARC are in place."""
    _on()
    async with db.connection() as conn:
        d = await service.domain(conn, domain_id)
    checks = await mail_dns.check_domain(d["name"], d["dkim_selector"])
    verified = checks.pop("verified")
    status = d["status"] if d["status"] == "suspended" else ("verified" if verified else "pending")
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """update mail_domains set checks = %s, checked_at = now(), status = %s,
                                          updated_at = now() where id = %s returning *""", (Jsonb(checks), status, domain_id))
        if status != d["status"]:
            await audit(conn, "mail.domain.verify", actor_id=p.user_id, meta=p.meta, table="mail_domains", row_id=domain_id,
                        before={"status": d["status"]}, after={"status": status})
    return _domain_out({**row, "mailboxes": None})


@router.patch("/domains/{domain_id}")
async def update_domain(domain_id: int, body: DomainPatch, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        d = await service.domain(conn, domain_id)
        after = {"status": body.status or d["status"], "dkim_selector": body.dkim_selector or d["dkim_selector"],
                 "lettermint_domain_id": body.lettermint_domain_id if body.lettermint_domain_id is not None
                 else d["lettermint_domain_id"]}
        extra = {}
        if after["status"] == "suspended" and d["status"] != "suspended":
            extra["reason"] = service.require_reason(body.reason)
        diff = {k: v for k, v in after.items() if d[k] != v}
        if diff:
            row = await db.fetch_one(conn, """update mail_domains set status = %s, dkim_selector = %s,
                                              lettermint_domain_id = %s, updated_at = now() where id = %s returning *""",
                                     (after["status"], after["dkim_selector"], after["lettermint_domain_id"], domain_id))
            await audit(conn, "mail.domain.update", actor_id=p.user_id, meta=p.meta, table="mail_domains",
                        row_id=domain_id, before={k: d[k] for k in diff}, after=diff, **extra)
            d = row
    return _domain_out({**d, "mailboxes": None})


# ── Postkasser ──────────────────────────────────────────────────────────────

@router.get("/mailboxes")
async def list_mailboxes(q: str | None = None, status: str | None = None, kind: str = "personal", limit: int = 100,
                         offset: int = 0):
    _on()
    where, params = ["m.kind = %s", "m.status <> 'deleted'"], [kind]
    if status:
        where.append("m.status = %s")
        params.append(status)
    if q:
        where.append("(m.local_part || '@' || d.name ilike %s or u.display_name ilike %s or u.email ilike %s)")
        params += [f"%{q}%"] * 3
    sql = service.MAILBOX_SQL + " where " + " and ".join(where)
    async with db.connection() as conn:
        total = (await db.fetch_one(conn, f"select count(*) as n from ({sql}) x", params))["n"]
        rows = await db.fetch_all(conn, sql + " order by address limit %s offset %s",
                                  params + [max(1, min(limit, 500)), max(0, offset)])
    items = [{k: r[k] for k in ("id", "address", "kind", "status", "owner_id", "owner_name", "owner_email", "display_name",
                                "quota_mb", "used_bytes", "last_login_at", "requested_at", "provisioned_at",
                                "provision_error", "drift")} for r in rows]
    return {"items": items, "total": total}


@router.get("/users-without-mailbox")
async def users_without_mailbox(q: str | None = None, limit: int = 200):
    _on()
    params: list = []
    where = ""
    if q:
        where = " and (u.display_name ilike %s or u.email ilike %s)"
        params = [f"%{q}%", f"%{q}%"]
    async with db.connection() as conn:
        return await db.fetch_all(conn, f"""
            select u.id, u.display_name, u.email, u.created_at from users u
            where not u.impersonation_only and u.blocked_at is null
              and not exists (select 1 from mailboxes m where m.owner_id = u.id and m.kind = 'personal'
                              and m.status <> 'deleted'){where}
            order by u.display_name nulls last, u.email limit %s""", params + [max(1, min(limit, 1000))])


@router.get("/people")
async def people(q: str | None = None, limit: int = 20):
    """User picker for members, shares and managers: name, e-mail and their mailbox address if any."""
    _on()
    params: list = []
    where = ""
    if q:
        where = " and (u.display_name ilike %s or u.email ilike %s or m.local_part || '@' || d.name ilike %s)"
        params = [f"%{q.strip()}%"] * 3
    async with db.connection() as conn:
        return await db.fetch_all(conn, f"""
            select u.id, u.display_name, u.email, m.local_part || '@' || d.name as mailbox, m.status as mailbox_status
            from users u
            left join mailboxes m on m.owner_id = u.id and m.kind = 'personal' and m.status <> 'deleted'
            left join mail_domains d on d.id = m.domain_id
            where not u.impersonation_only and u.blocked_at is null{where}
            order by u.display_name nulls last, u.email limit %s""", params + [max(1, min(limit, 100))])


class BulkIn(BaseModel):
    user_ids: list[int] = Field(min_length=1, max_length=500)
    domain_id: int
    quota_mb: int | None = Field(default=None, ge=10, le=1048576)


@router.post("/mailboxes/bulk")
async def bulk_provision(body: BulkIn, p: Principal = Depends(require_admin)):
    """One personal mailbox per user (firstname.lastname@domain); Stalwart is called by the job mail.provision."""
    _on()
    created, skipped = [], []
    async with db.connection() as conn:
        d = await service.domain(conn, body.domain_id)
        for uid in dict.fromkeys(body.user_ids):
            user = await db.fetch_one(conn, "select id, display_name, email from users where id = %s "
                                            "and not impersonation_only", (uid,))
            if user is None or await service.personal_mailbox(conn, uid):
                skipped.append(uid)
                continue
            local = await service.default_local_part(conn, user, d)
            mb = await service.request_mailbox(conn, kind="personal", domain_row=d, local_part=local, actor_id=p.user_id,
                                               meta=p.meta, owner_id=uid, quota_mb=body.quota_mb)
            created.append({"user_id": uid, "mailbox_id": mb["id"], "address": mb["address"]})
    return {"created": created, "skipped": skipped}


# ── One user (/admin/mail/users/<id>) ───────────────────────────────────────

@router.get("/users/{user_id}")
async def get_user(user_id: int):
    _on()
    async with db.connection() as conn:
        user = await _user(conn, user_id)
        out = await overview(conn, user_id)
        mb = await service.personal_mailbox(conn, user_id)
        out["user"] = user
        out["provisioning"] = {k: mb[k] for k in ("stalwart_principal", "status", "requested_at", "provisioned_at",
                                                  "synced_at", "drift", "provision_error", "created_at")} if mb else None
        out["app_passwords"] = await service.app_passwords(conn, user_id)
        out["collections"] = await service.collections_for(conn, user_id)
        out["availability"] = await availability(conn, user_id)
        out["activity"] = await activity(conn, user_id)
        out["audit"] = await db.fetch_all(conn, """
            select a.ts, a.action, a.table_name, a.before, a.after, a.detail, u.display_name as actor_name,
                   u.email as actor_email
            from audit_log a left join users u on u.id = a.actor_user_id
            where a.action like 'mail.%%'
              and ((a.table_name = 'mailboxes' and a.row_id = %s::text)
                   or (a.after ->> 'user_id')::bigint = %s or (a.before ->> 'user_id')::bigint = %s
                   or (a.table_name = 'mail_collections' and a.row_id in
                       (select id::text from mail_collections where owner_id = %s)))
            order by a.ts desc limit 100""", (mb["id"] if mb else 0, user_id, user_id, user_id))
    return out


class MailboxIn(BaseModel):
    domain_id: int
    local_part: str | None = Field(default=None, max_length=64)
    quota_mb: int | None = Field(default=None, ge=10, le=1048576)


@router.post("/users/{user_id}/mailbox")
async def create_mailbox(user_id: int, body: MailboxIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        user = await _user(conn, user_id)
        d = await service.domain(conn, body.domain_id)
        local = body.local_part or await service.default_local_part(conn, user, d)
        mb = await service.request_mailbox(conn, kind="personal", domain_row=d, local_part=local, actor_id=p.user_id,
                                           meta=p.meta, owner_id=user_id, quota_mb=body.quota_mb)
    # Usually immediate; if Stalwart is down, the queued job retries and the page says it is being set up.
    try:
        await service.provision(mb["id"])
    except MailBackendError:
        pass
    async with db.connection() as conn:
        return await overview(conn, user_id)


class MailboxPatch(BaseModel):
    quota_mb: int | None = Field(default=None, ge=10, le=1048576)
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    reason: str | None = Field(default=None, max_length=500)


def _stepup(p: Principal) -> None:
    from app import mfa
    if not mfa.within(p.mfa_at, timedelta(minutes=config.settings.stepup_minutes)):
        raise mfa.mfa_error("stepup_required", "Bekræft med din passkey, sikkerhedsnøgle eller godkendelsesapp først")


@router.patch("/users/{user_id}/mailbox")
async def update_mailbox(user_id: int, body: MailboxPatch, p: Principal = Depends(require_admin)):
    """Quota and status. Suspending stops login and inbound delivery; the mail stays until retention removes it."""
    _on()
    async with db.connection() as conn:
        mb = await service.personal_mailbox(conn, user_id)
        if mb is None:
            raise HTTPException(404, "Brugeren har ingen postkasse")
        mb = await service.mailbox(conn, mb["id"], lock=True)
        return await _patch_mailbox(conn, mb, body, p)


async def _patch_mailbox(conn, mb: dict, body: MailboxPatch, p: Principal) -> dict:
    after = {"quota_mb": body.quota_mb or mb["quota_mb"], "status": body.status or mb["status"]}
    if mb["status"] == "provisioning" and body.status:
        raise HTTPException(409, "Postkassen bliver stadig sat op")
    diff = {k: v for k, v in after.items() if mb[k] != v}
    if not diff:
        return {"ok": True}
    extra = {}
    if diff.get("status") == "suspended":
        _stepup(p)
        extra["reason"] = service.require_reason(body.reason)
    await conn.execute("update mailboxes set quota_mb = %s, status = %s, updated_at = now() where id = %s",
                       (after["quota_mb"], after["status"], mb["id"]))
    await audit(conn, "mail.mailbox.suspend" if diff.get("status") == "suspended" else "mail.mailbox.update",
                actor_id=p.user_id, meta=p.meta, table="mailboxes", row_id=mb["id"],
                before={k: mb[k] for k in diff}, after={**diff, "address": mb["address"]}, **extra)
    if diff.get("status") == "suspended":
        await webhooks.emit(conn, "mail.mailbox.suspended", {"mailbox_id": mb["id"]})
    await service.push_mailbox(conn, {**mb, **after})
    return {"ok": True}


class AliasIn(BaseModel):
    address: str = Field(min_length=3, max_length=254)


async def add_alias(conn, mb: dict, address: str, p: Principal) -> dict:
    address = service.clean_address(address)
    await service.domain_for_address(conn, address)
    await service.ensure_free(conn, address)
    row = await db.fetch_one(conn, "insert into mail_aliases (mailbox_id, address, created_by) values (%s, %s, %s) "
                                   "returning id, address, created_at", (mb["id"], address, p.user_id))
    await audit(conn, "mail.alias.create", actor_id=p.user_id, meta=p.meta, table="mail_aliases", row_id=row["id"],
                after={"address": address, "mailbox": mb["address"], "user_id": mb["owner_id"]})
    await service.push_mailbox(conn, mb)
    if mb["owner_id"]:
        await service.sync_identities(conn, mb["owner_id"])
    else:
        for m in await db.fetch_all(conn, "select user_id from mail_shared_members where mailbox_id = %s", (mb["id"],)):
            await service.sync_identities(conn, m["user_id"])
    return row


@router.post("/users/{user_id}/aliases")
async def create_alias(user_id: int, body: AliasIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.personal_mailbox(conn, user_id)
        if mb is None:
            raise HTTPException(404, "Brugeren har ingen postkasse")
        await add_alias(conn, await service.mailbox(conn, mb["id"], lock=True), body.address, p)
        return await service.aliases(conn, mb["id"])


async def remove_alias(conn, mb: dict, alias_id: int, p: Principal) -> None:
    row = await db.fetch_one(conn, "delete from mail_aliases where id = %s and mailbox_id = %s returning address",
                             (alias_id, mb["id"]))
    if row is None:
        raise HTTPException(404, "Aliasset findes ikke")
    await audit(conn, "mail.alias.delete", actor_id=p.user_id, meta=p.meta, table="mail_aliases", row_id=alias_id,
                before={"address": row["address"], "mailbox": mb["address"], "user_id": mb["owner_id"]})
    await service.push_mailbox(conn, mb)
    users = [mb["owner_id"]] if mb["owner_id"] else [m["user_id"] for m in await db.fetch_all(
        conn, "select user_id from mail_shared_members where mailbox_id = %s", (mb["id"],))]
    for u in users:
        await service.sync_identities(conn, u)


@router.delete("/users/{user_id}/aliases/{alias_id}")
async def delete_alias(user_id: int, alias_id: int, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.personal_mailbox(conn, user_id)
        if mb is None:
            raise HTTPException(404, "Brugeren har ingen postkasse")
        await remove_alias(conn, await service.mailbox(conn, mb["id"], lock=True), alias_id, p)
        return await service.aliases(conn, mb["id"])


@router.get("/alias-requests")
async def alias_requests(status: str = "pending"):
    _on()
    async with db.connection() as conn:
        return await db.fetch_all(conn, """
            select r.id, r.address, r.note, r.status, r.created_at, r.decided_at, r.user_id, u.display_name, u.email
            from mail_alias_requests r join users u on u.id = r.user_id
            where r.status = %s order by r.created_at desc limit 200""", (status,))


class DecisionIn(BaseModel):
    approve: bool


@router.post("/alias-requests/{request_id}")
async def decide_alias_request(request_id: int, body: DecisionIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        req = await db.fetch_one(conn, "select * from mail_alias_requests where id = %s and status = 'pending' for update",
                                 (request_id,))
        if req is None:
            raise HTTPException(404, "Anmodningen findes ikke eller er besvaret")
        if body.approve:
            await add_alias(conn, await service.mailbox(conn, req["mailbox_id"], lock=True), req["address"], p)
        status = "approved" if body.approve else "declined"
        await conn.execute("update mail_alias_requests set status = %s, decided_by = %s, decided_at = now() where id = %s",
                           (status, p.user_id, request_id))
        await audit(conn, "mail.alias.request_decided", actor_id=p.user_id, meta=p.meta, table="mail_alias_requests",
                    row_id=request_id, after={"status": status, "address": req["address"], "user_id": req["user_id"]})
    return {"ok": True, "status": status}


@router.get("/users/{user_id}/app-passwords")
async def user_app_passwords(user_id: int):
    _on()
    async with db.connection() as conn:
        return await service.app_passwords(conn, user_id)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/users/{user_id}/app-passwords/revoke-all")
async def revoke_all(user_id: int, body: ReasonIn, p: Principal = Depends(require_admin_stepup)):
    """Signs every client out: app passwords are the only way clients log in."""
    _on()
    reason = service.require_reason(body.reason)
    async with db.connection() as conn:
        await _user(conn, user_id)
        n = await service.revoke_app_passwords(conn, user_id, actor_id=p.user_id, meta=p.meta, reason=reason)
    return {"revoked": n}


class MembershipIn(BaseModel):
    mailbox_id: int
    role: str = Field(pattern="^(reader|sender|manager)$")


@router.post("/users/{user_id}/shared-mailboxes")
async def add_to_shared(user_id: int, body: MembershipIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.mailbox(conn, body.mailbox_id, kind="shared", lock=True)
        await service.set_member(conn, mb, user_id, body.role, actor_id=p.user_id, meta=p.meta, admin=True)
        return await service.memberships(conn, user_id)


class TransferIn(BaseModel):
    managers: list[int] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/users/{user_id}/transfer-collections")
async def transfer(user_id: int, body: TransferIn, p: Principal = Depends(require_admin_stepup)):
    _on()
    reason = service.require_reason(body.reason)
    async with db.connection() as conn:
        await _user(conn, user_id)
        moved = await service.transfer_collections(conn, user_id, actor_id=p.user_id, meta=p.meta, members=body.managers)
        await audit(conn, "mail.offboard.transfer", actor_id=p.user_id, meta=p.meta, table="users", row_id=user_id,
                    after={"collections": len(moved), "managers": body.managers}, reason=reason)
    return {"moved": moved}


class OffboardIn(BaseModel):
    action: str = Field(pattern="^(suspend|convert|delete)$")
    shared_mailbox_id: int | None = None          # convert: the shared mailbox that gets the address as an alias
    collections: str = Field(default="transfer", pattern="^(transfer|delete)$")
    managers: list[int] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/users/{user_id}/offboard")
async def offboard(user_id: int, body: OffboardIn, p: Principal = Depends(require_admin_stepup)):
    """suspend: no login, no inbound mail; mail kept until retention removes it.
    convert: the address becomes an alias on a shared mailbox, so mail to the leaver lands with the team.
    convert and delete remove the mailbox in Stalwart; first the calendars and address books are transferred to shared
    ones (managers get them) or deleted."""
    _on()
    reason = service.require_reason(body.reason)
    async with db.connection() as conn:
        await _user(conn, user_id)
        mb = await service.personal_mailbox(conn, user_id)
        if mb is None:
            raise HTTPException(404, "Brugeren har ingen postkasse")
        mb = await service.mailbox(conn, mb["id"], lock=True)
        if body.action == "suspend":
            return await _patch_mailbox(conn, mb, MailboxPatch(status="suspended", reason=reason), p)
        target = None
        if body.action == "convert":
            if not body.shared_mailbox_id:
                raise HTTPException(400, "Vælg den fælles postkasse, adressen skal lande i")
            target = await service.mailbox(conn, body.shared_mailbox_id, kind="shared", lock=True)
        if body.collections == "transfer":
            moved = await service.transfer_collections(conn, user_id, actor_id=p.user_id, meta=p.meta,
                                                       members=body.managers)
        else:
            moved = []
            for col in await db.fetch_all(conn, f"select {service.COLLECTION_COLS} from mail_collections c "
                                                "where c.owner_id = %s", (user_id,)):
                await service.delete_collection(conn, {**col, "is_default": False}, actor_id=p.user_id, meta=p.meta,
                                                reason=reason)
        addresses = await service.principal_emails(conn, mb)
        await service.revoke_app_passwords(conn, user_id, actor_id=p.user_id, meta=p.meta, reason=reason)
        await conn.execute("delete from mail_aliases where mailbox_id = %s", (mb["id"],))
        await conn.execute("update mailboxes set status = 'deleted', updated_at = now() where id = %s", (mb["id"],))
        await conn.execute("delete from mail_identities where mailbox_id = %s", (mb["id"],))
        try:
            await service.backend().delete_principal(mb["stalwart_principal"])
        except MailBackendError as e:
            raise service.backend_error(e)
        await audit(conn, "mail.mailbox.delete", actor_id=p.user_id, meta=p.meta, table="mailboxes", row_id=mb["id"],
                    before={"address": mb["address"], "aliases": addresses[1:]},
                    after={"action": body.action, "collections": body.collections, "moved": len(moved),
                           "converted_to": target["address"] if target else None}, reason=reason)
        await webhooks.emit(conn, "mail.mailbox.deleted", {"mailbox_id": mb["id"]})
        if target:
            for address in addresses:
                await conn.execute("insert into mail_aliases (mailbox_id, address, created_by) values (%s, %s, %s)",
                                   (target["id"], address, p.user_id))
            await audit(conn, "mail.alias.create", actor_id=p.user_id, meta=p.meta, table="mail_aliases",
                        row_id=target["id"], after={"addresses": addresses, "mailbox": target["address"]},
                        via="offboard", reason=reason)
            await service.push_mailbox(conn, target)
            for m in await db.fetch_all(conn, "select user_id from mail_shared_members where mailbox_id = %s",
                                        (target["id"],)):
                await service.sync_identities(conn, m["user_id"])
        await service.sync_identities(conn, user_id)
    return {"ok": True, "moved": moved}


# ── Fælles postkasser ───────────────────────────────────────────────────────

class SharedIn(BaseModel):
    domain_id: int
    local_part: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    quota_mb: int | None = Field(default=None, ge=10, le=1048576)
    managers: list[int] = Field(default_factory=list, max_length=50)


class SharedAdminPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    quota_mb: int | None = Field(default=None, ge=10, le=1048576)


@router.get("/shared")
async def list_shared():
    _on()
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, service.MAILBOX_SQL + " where m.kind = 'shared' and m.status <> 'deleted' "
                                                              "order by address")
        out = []
        for r in rows:
            out.append({**shared_out(r, await service.members(conn, r["id"])),
                        "aliases": await service.aliases(conn, r["id"]), "provision_error": r["provision_error"]})
    return out


@router.post("/shared")
async def create_shared(body: SharedIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        d = await service.domain(conn, body.domain_id)
        mb = await service.request_mailbox(conn, kind="shared", domain_row=d, local_part=body.local_part,
                                           actor_id=p.user_id, meta=p.meta, display_name=body.display_name.strip(),
                                           quota_mb=body.quota_mb)
        for uid in dict.fromkeys(body.managers):
            await service.set_member(conn, mb, uid, "manager", actor_id=p.user_id, meta=p.meta, admin=True)
    try:
        await service.provision(mb["id"])
    except MailBackendError:
        pass
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mb["id"])
        return shared_out(mb, await service.members(conn, mb["id"]))


@router.patch("/shared/{mailbox_id}")
async def update_shared(mailbox_id: int, body: SharedAdminPatch, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mailbox_id, kind="shared", lock=True)
        after = {"display_name": (body.display_name or mb["display_name"] or "").strip(),
                 "quota_mb": body.quota_mb or mb["quota_mb"]}
        diff = {k: v for k, v in after.items() if mb[k] != v}
        if diff:
            await conn.execute("update mailboxes set display_name = %s, quota_mb = %s, updated_at = now() where id = %s",
                               (after["display_name"], after["quota_mb"], mailbox_id))
            await audit(conn, "mail.shared.update", actor_id=p.user_id, meta=p.meta, table="mailboxes", row_id=mailbox_id,
                        before={k: mb[k] for k in diff}, after=diff)
            if mb["status"] != "provisioning":
                try:
                    await service.backend().update_principal(mb["stalwart_principal"], quota_mb=after["quota_mb"],
                                                             description=after["display_name"])
                except MailBackendError as e:
                    raise service.backend_error(e)
        return shared_out(await service.mailbox(conn, mailbox_id), await service.members(conn, mailbox_id))


@router.delete("/shared/{mailbox_id}")
async def delete_shared(mailbox_id: int, reason: str = "", p: Principal = Depends(require_admin_stepup)):
    _on()
    reason = service.require_reason(reason)
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mailbox_id, kind="shared", lock=True)
        members = [m["user_id"] for m in await service.members(conn, mailbox_id)]
        addresses = await service.principal_emails(conn, mb)
        await conn.execute("delete from mail_shared_members where mailbox_id = %s", (mailbox_id,))
        await conn.execute("delete from mail_aliases where mailbox_id = %s", (mailbox_id,))
        await conn.execute("update mailboxes set status = 'deleted', updated_at = now() where id = %s", (mailbox_id,))
        for u in members:
            await service.sync_identities(conn, u)
        try:
            await service.backend().delete_principal(mb["stalwart_principal"])
        except MailBackendError as e:
            raise service.backend_error(e)
        await audit(conn, "mail.shared.delete", actor_id=p.user_id, meta=p.meta, table="mailboxes", row_id=mailbox_id,
                    before={"addresses": addresses, "members": len(members)}, reason=reason)
        await webhooks.emit(conn, "mail.mailbox.deleted", {"mailbox_id": mailbox_id})
    return {"ok": True}


@router.post("/shared/{mailbox_id}/aliases")
async def shared_alias(mailbox_id: int, body: AliasIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mailbox_id, kind="shared", lock=True)
        await add_alias(conn, mb, body.address, p)
        return await service.aliases(conn, mailbox_id)


@router.delete("/shared/{mailbox_id}/aliases/{alias_id}")
async def shared_alias_delete(mailbox_id: int, alias_id: int, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mailbox_id, kind="shared", lock=True)
        await remove_alias(conn, mb, alias_id, p)
        return await service.aliases(conn, mailbox_id)


class MemberRole(BaseModel):
    role: str = Field(pattern="^(reader|sender|manager)$")


@router.put("/shared/{mailbox_id}/members/{user_id}")
async def put_member(mailbox_id: int, user_id: int, body: MemberRole, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mailbox_id, kind="shared", lock=True)
        await service.set_member(conn, mb, user_id, body.role, actor_id=p.user_id, meta=p.meta, admin=True)
        return await service.members(conn, mailbox_id)


@router.delete("/shared/{mailbox_id}/members/{user_id}")
async def delete_member(mailbox_id: int, user_id: int, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        mb = await service.mailbox(conn, mailbox_id, kind="shared", lock=True)
        await service.set_member(conn, mb, user_id, None, actor_id=p.user_id, meta=p.meta, admin=True)
        return await service.members(conn, mailbox_id)


# ── Kalendere og kontakter ──────────────────────────────────────────────────

class SharedCollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    timezone: str | None = Field(default=None, max_length=60)
    managers: list[int] = Field(default_factory=list, max_length=50)


class CollectionAdminPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    timezone: str | None = Field(default=None, max_length=60)


@router.get("/collections")
async def list_collections():
    """Shared calendars and address books with members, and how many each user has."""
    _on()
    async with db.connection() as conn:
        shared = await db.fetch_all(conn, f"select {service.COLLECTION_COLS} from mail_collections c "
                                          "where c.kind = 'shared' order by c.type, c.name")
        for c in shared:
            c["shares"] = await service.shares(conn, c["id"])
        per_user = await db.fetch_all(conn, """
            select u.id as user_id, u.display_name, u.email,
                   count(*) filter (where c.type = 'calendar') as calendars,
                   count(*) filter (where c.type = 'address_book') as address_books,
                   coalesce(sum(c.entry_count) filter (where c.type = 'address_book'), 0) as contacts
            from mail_collections c join users u on u.id = c.owner_id
            group by u.id order by u.display_name nulls last, u.email limit 500""")
    return {"shared": shared, "per_user": per_user}


async def _create_shared_collection(type: str, body: SharedCollectionIn, p: Principal) -> dict:
    _on()
    group = f"shared-{type.replace('_', '-')}-{secrets.token_hex(4)}"
    try:
        await service.backend().ensure_group(group, emails=[], description=body.name.strip())
    except MailBackendError as e:
        raise service.backend_error(e)
    async with db.connection() as conn:
        col = await service.create_collection(
            conn, type=type, kind="shared", owner_id=None, owner_principal=group, name=body.name.strip(),
            colour=body.colour if type == CALENDAR else None,
            timezone=(body.timezone or "Europe/Copenhagen") if type == CALENDAR else None, actor_id=p.user_id,
            meta=p.meta, slug="default")
        for uid in dict.fromkeys(body.managers):
            await service.set_collection_share(conn, col, uid, "manager", actor_id=p.user_id, meta=p.meta)
        col["shares"] = await service.shares(conn, col["id"])
    return col


@router.post("/shared-calendars")
async def create_shared_calendar(body: SharedCollectionIn, p: Principal = Depends(require_admin)):
    return await _create_shared_collection(CALENDAR, body, p)


@router.post("/shared-address-books")
async def create_shared_address_book(body: SharedCollectionIn, p: Principal = Depends(require_admin)):
    return await _create_shared_collection(ADDRESS_BOOK, body, p)


@router.patch("/collections/{collection_id}")
async def update_collection(collection_id: str, body: CollectionAdminPatch, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        col = await service.collection(conn, collection_id, lock=True)
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        return await service.update_collection(conn, col, changes, actor_id=p.user_id, meta=p.meta)


@router.delete("/collections/{collection_id}")
async def delete_collection(collection_id: str, reason: str = "", p: Principal = Depends(require_admin_stepup)):
    _on()
    reason = service.require_reason(reason)
    async with db.connection() as conn:
        col = await service.collection(conn, collection_id, lock=True)
        await service.delete_collection(conn, {**col, "is_default": False}, actor_id=p.user_id, meta=p.meta, reason=reason)
    return {"ok": True}


class ShareRole(BaseModel):
    role: str = Field(pattern="^(viewer|editor|manager)$")


@router.put("/collections/{collection_id}/shares/{user_id}")
async def put_share(collection_id: str, user_id: int, body: ShareRole, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        col = await service.collection(conn, collection_id, lock=True)
        await service.set_collection_share(conn, col, user_id, body.role, actor_id=p.user_id, meta=p.meta)
        return await service.shares(conn, col["id"])


@router.delete("/collections/{collection_id}/shares/{user_id}")
async def delete_share(collection_id: str, user_id: int, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        col = await service.collection(conn, collection_id, lock=True)
        await service.set_collection_share(conn, col, user_id, None, actor_id=p.user_id, meta=p.meta)
        return await service.shares(conn, col["id"])


# ── Afsendelse (Lettermint) ─────────────────────────────────────────────────

@router.get("/sending")
async def sending():
    _on()
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        r24, r30 = await rates(conn, 24), await rates(conn, 24 * 30)
        recent = await db.fetch_all(conn, """select recipient_domain, type, at from mail_events
                                             where type in ('bounced', 'complained') order by at desc limit 50""")
        endpoints = await db.fetch_all(conn, """select id, name, active, received_count, last_received_at
                                                from webhook_endpoints where handler = 'lettermint' order by id""")
    rotated = values["mail_relay_rotated_at"]
    age = None
    if rotated:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(rotated)).days
    return {"relay_host": config.settings.lettermint_smtp_host, "api_token_set": bool(config.settings.lettermint_api_token),
            "rotated_at": rotated, "credentials_age_days": age, "paused": bool(values["mail_relay_paused"]),
            "rates_24h": r24, "rates_30d": r30, "recent_problems": recent, "webhook_endpoints": endpoints,
            "thresholds": {"bounce": BOUNCE_WARN, "complaint": COMPLAINT_WARN}}


class RelayIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/sending/rotate")
async def rotate_relay(body: RelayIn, p: Principal = Depends(require_admin_stepup)):
    """Puts new Lettermint SMTP credentials into Stalwart's relay route. The secret is neither stored nor logged."""
    _on()
    reason = service.require_reason(body.reason)
    try:
        await service.backend().set_relay_credentials(body.username.strip(), body.password)
    except MailBackendError as e:
        raise service.backend_error(e)
    now = datetime.now(timezone.utc).isoformat()
    async with db.connection() as conn:
        await settings_store.set_many(conn, {"mail_relay_rotated_at": now})
        await audit(conn, "mail.relay.rotate", actor_id=p.user_id, meta=p.meta, table="settings",
                    row_id="mail_relay_rotated_at", after={"username": body.username.strip(), "at": now}, reason=reason)
    return {"rotated_at": now}


# ── Opbevaring ──────────────────────────────────────────────────────────────

class PolicyIn(BaseModel):
    days: int = Field(ge=1, le=service.MAX_RETENTION_DAYS)
    applies_to: str = Field(default="all", pattern="^(all|trash|junk)$")


class RetentionIn(PolicyIn):
    archive_export: bool | None = None


@router.get("/retention")
async def get_retention():
    _on()
    async with db.connection() as conn:
        policies = await db.fetch_all(conn, """
            select p.id, p.scope, p.days, p.applies_to, p.updated_at, p.domain_id, d.name as domain, p.mailbox_id,
                   m.local_part || '@' || md.name as mailbox
            from mail_retention_policies p left join mail_domains d on d.id = p.domain_id
            left join mailboxes m on m.id = p.mailbox_id left join mail_domains md on md.id = m.domain_id
            order by case p.scope when 'global' then 0 when 'domain' then 1 else 2 end, d.name, mailbox""")
        runs = await db.fetch_all(conn, """select r.id, r.dry_run, r.status, r.mailboxes, r.expired, r.archived, r.failures,
                                                  r.started_at, r.finished_at, u.display_name as triggered_by
                                           from mail_retention_runs r left join users u on u.id = r.triggered_by
                                           order by r.id desc limit 30""")
        values = await settings_store.get_all(conn)
        schedule = await db.fetch_one(conn, "select enabled, next_run_at, last_run_at from job_schedules "
                                            "where name = 'mail.retention'")
    return {"max_days": service.MAX_RETENTION_DAYS, "policies": policies, "runs": runs,
            "archive_export": bool(values["mail_archive_export"]), "schedule": schedule,
            "last_backup": values["mail_last_backup"]}


@router.put("/retention")
async def put_retention(body: RetentionIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        before = await db.fetch_one(conn, "select days, applies_to from mail_retention_policies where scope = 'global'")
        await conn.execute("""insert into mail_retention_policies (scope, days, applies_to, updated_by)
                              values ('global', %s, %s, %s)
                              on conflict (scope, coalesce(domain_id, 0), coalesce(mailbox_id, 0))
                              do update set days = excluded.days, applies_to = excluded.applies_to,
                                            updated_by = excluded.updated_by, updated_at = now()""",
                           (body.days, body.applies_to, p.user_id))
        values = await settings_store.get_all(conn)
        after = {"days": body.days, "applies_to": body.applies_to}
        b = dict(before or {})
        if body.archive_export is not None and body.archive_export != bool(values["mail_archive_export"]):
            await settings_store.set_many(conn, {"mail_archive_export": body.archive_export})
            b["archive_export"], after["archive_export"] = bool(values["mail_archive_export"]), body.archive_export
        await audit(conn, "mail.retention.update", actor_id=p.user_id, meta=p.meta, table="mail_retention_policies",
                    row_id="global", before=b, after=after)
    return await get_retention()


@router.put("/retention/domains/{domain_id}")
async def put_domain_policy(domain_id: int, body: PolicyIn, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        d = await service.domain(conn, domain_id)
        await conn.execute("""insert into mail_retention_policies (scope, domain_id, days, applies_to, updated_by)
                              values ('domain', %s, %s, %s, %s)
                              on conflict (scope, coalesce(domain_id, 0), coalesce(mailbox_id, 0))
                              do update set days = excluded.days, applies_to = excluded.applies_to,
                                            updated_by = excluded.updated_by, updated_at = now()""",
                           (domain_id, body.days, body.applies_to, p.user_id))
        await audit(conn, "mail.retention.update", actor_id=p.user_id, meta=p.meta, table="mail_retention_policies",
                    row_id=f"domain:{domain_id}", after={"domain": d["name"], "days": body.days,
                                                         "applies_to": body.applies_to})
    return await get_retention()


@router.delete("/retention/domains/{domain_id}")
async def delete_domain_policy(domain_id: int, p: Principal = Depends(require_admin)):
    _on()
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from mail_retention_policies where scope = 'domain' and domain_id = %s "
                                       "returning days, applies_to", (domain_id,))
        if row is None:
            raise HTTPException(404, "Domænet har ingen egen regel")
        await audit(conn, "mail.retention.delete", actor_id=p.user_id, meta=p.meta, table="mail_retention_policies",
                    row_id=f"domain:{domain_id}", before=row)
    return await get_retention()


@router.post("/retention/run")
async def run_retention(dry_run: bool = False, dryRun: bool = False, p: Principal = Depends(require_admin)):  # noqa: N803
    """dry_run=true (or dryRun=true) only counts what would expire. A real run is queued as the job mail.retention."""
    _on()
    if dry_run or dryRun:
        return await service.run_retention(dry_run=True, triggered_by=p.user_id, meta=p.meta)
    async with db.connection() as conn:
        job = await jobs.enqueue(conn, "mail.retention", {"triggered_by": p.user_id}, dedupe_key="mail.retention.manual",
                                 max_attempts=1)
        await audit(conn, "mail.retention.queued", actor_id=p.user_id, meta=p.meta, job_id=job)
    return {"queued": job is not None, "job_id": job}


# ── Drift ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    _on()
    h, cert = await asyncio.gather(_health(), cert_expiry(config.settings.mail_hostname))
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        drift = await db.fetch_all(conn, service.MAILBOX_SQL + " where m.drift is not null and m.status <> 'deleted' "
                                                               "order by address limit 100")
        failing = await db.fetch_all(conn, service.MAILBOX_SQL + " where m.provision_error is not null "
                                                                 "and m.status = 'provisioning' order by address limit 100")
        schedules = await db.fetch_all(conn, """select name, enabled, interval_seconds, next_run_at, last_run_at
                                                from job_schedules where name like 'mail.%%' order by name""")
    return {"backend": config.settings.mailbox_backend, "hostname": config.settings.mail_hostname, "stalwart": h,
            "certificate": cert, "paused": bool(values["mail_relay_paused"]), "last_sync": values["mail_last_sync"],
            "last_backup": values["mail_last_backup"],
            "drift": [{"id": d["id"], "address": d["address"], "drift": d["drift"]} for d in drift],
            "provisioning_errors": [{"id": d["id"], "address": d["address"], "error": d["provision_error"]} for d in failing],
            "schedules": schedules}


@router.post("/health/queue/{action}")
async def queue_action(action: str, p: Principal = Depends(require_admin_stepup)):
    """pause / resume the outbound relay, or flush (retry everything in the queue now)."""
    _on()
    if action not in ("pause", "resume", "flush"):
        raise HTTPException(404, "Ukendt handling")
    try:
        await service.backend().queue_action(action)
    except MailBackendError as e:
        raise service.backend_error(e)
    async with db.connection() as conn:
        if action in ("pause", "resume"):
            await settings_store.set_many(conn, {"mail_relay_paused": action == "pause"})
        await audit(conn, f"mail.queue.{action}", actor_id=p.user_id, meta=p.meta)
    return {"ok": True}


@router.post("/health/sync")
async def restart_sync(p: Principal = Depends(require_admin)):
    """Queues the sync now, and re-queues provisioning of mailboxes that failed."""
    _on()
    async with db.connection() as conn:
        job = await jobs.enqueue(conn, "mail.sync", {"triggered_by": p.user_id}, dedupe_key="mail.sync.manual",
                                 max_attempts=1)
        stuck = await db.fetch_all(conn, "select id from mailboxes where status = 'provisioning'")
        for m in stuck:
            await jobs.enqueue(conn, "mail.provision", {"mailbox_id": m["id"]}, dedupe_key=f"mail.provision:{m['id']}",
                               max_attempts=10)
        await audit(conn, "mail.sync.queued", actor_id=p.user_id, meta=p.meta, job_id=job, provisioning=len(stuck))
    return {"queued": job is not None, "provisioning": len(stuck)}


# ── Audit ───────────────────────────────────────────────────────────────────

@router.get("/audit")
async def audit_log(actor: str | None = None, mailbox: str | None = None, action: str | None = None, limit: int = 100,
                    offset: int = 0, format: str = "json"):
    """Every admin and manager action in the module (audit entries mail.*), filterable; format=csv exports."""
    _on()
    where, params = ["a.action like 'mail.%%'"], []
    if actor:
        where.append("(u.email ilike %s or u.display_name ilike %s or a.actor_user_id::text = %s)")
        params += [f"%{actor}%", f"%{actor}%", actor]
    if mailbox:
        where.append("(a.after::text ilike %s or a.before::text ilike %s or a.detail::text ilike %s)")
        params += [f"%{mailbox}%"] * 3
    if action:
        where.append("a.action like %s")
        params.append(f"mail.{action.removeprefix('mail.')}%")
    sql = f"""select a.id, a.ts, a.action, a.table_name, a.row_id, a.before, a.after, a.detail,
                     a.actor_user_id, u.display_name as actor_name, u.email as actor_email
              from audit_log a left join users u on u.id = a.actor_user_id where {' and '.join(where)}"""
    async with db.connection() as conn:
        if format == "csv":
            rows = await db.fetch_all(conn, sql + " order by a.ts desc limit 50000", params)
        else:
            total = (await db.fetch_one(conn, f"select count(*) as n from ({sql}) x", params))["n"]
            rows = await db.fetch_all(conn, sql + " order by a.ts desc limit %s offset %s",
                                      params + [max(1, min(limit, 500)), max(0, offset)])
    if format != "csv":
        return {"items": rows, "total": total}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["tidspunkt", "handling", "aktør", "tabel", "række", "før", "efter", "detaljer"])
    for r in rows:
        w.writerow([r["ts"].isoformat(), r["action"], r["actor_email"] or r["actor_name"] or "system", r["table_name"] or "",
                    r["row_id"] or "", json.dumps(r["before"], ensure_ascii=False) if r["before"] else "",
                    json.dumps(r["after"], ensure_ascii=False) if r["after"] else "",
                    json.dumps(r["detail"], ensure_ascii=False) if r["detail"] else ""])
    filename = f"mail-audit-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
