"""/admin/webhooks and /admin/jobs: outgoing webhooks, the job queue, schedules and retention policies.

Creating an endpoint, changing where it sends or what it sends with, and rotating its secret need a recent second
factor (require_admin_stepup): a webhook can send data out of the app. Secrets and auth headers are never returned
after creation, only the last four characters of the header value.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import config, db, jobs, retention, secrets_box, webhooks, webhooks_in
from app.logs import audit, changed
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

HOOK_COLUMNS = ("id, name, url, events, active, auth_header_name, auth_header_hint, timeout_seconds, max_attempts, "
                "consecutive_failures, disabled_reason, created_at, updated_at, last_success_at, last_failure_at")


def _hint(value: str) -> str:
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def _events(events: list[str]) -> list[str]:
    unknown = [e for e in events if e != "*" and e not in webhooks.EVENTS]
    if unknown:
        raise HTTPException(400, f"Ukendte hændelser: {', '.join(unknown)}")
    if not events:
        raise HTTPException(400, "Vælg mindst én hændelse")
    return sorted(set(events))


def _url(url: str) -> str:
    try:
        return webhooks.check_url(url)
    except webhooks.UnsafeUrl as e:
        raise HTTPException(400, str(e))


# ── Webhooks ────────────────────────────────────────────────────────────────

@router.get("/webhooks")
async def list_webhooks():
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""
            select {HOOK_COLUMNS},
                   (select count(*) from webhook_deliveries d where d.webhook_id = w.id
                      and d.created_at > now() - interval '24 hours') as deliveries_24h,
                   (select count(*) from webhook_deliveries d where d.webhook_id = w.id and d.status = 'dead'
                      and d.created_at > now() - interval '24 hours') as dead_24h
            from webhooks w order by w.created_at""")
    return {"items": items, "events": [{"key": k, "description": v} for k, v in webhooks.EVENTS.items()]}


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=2000)
    events: list[str] = Field(min_length=1, max_length=50)
    active: bool = True
    # Optional header sent with every delivery, e.g. Authorization: Bearer …  Empty value = no header.
    auth_header_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")
    auth_header_value: str | None = Field(default=None, max_length=2000)
    timeout_seconds: int = Field(default=10, ge=1, le=30)
    max_attempts: int = Field(default=8, ge=1, le=20)


@router.post("/webhooks", status_code=201)
async def create_webhook(body: WebhookIn, admin: Principal = Depends(require_admin_stepup)):
    url, events = _url(body.url), _events(body.events)
    secret = webhooks.new_secret()
    header = (body.auth_header_value or "").strip()
    header_name = (body.auth_header_name or "Authorization") if header else None
    async with db.connection() as conn:
        row = await db.fetch_one(conn, f"""
            insert into webhooks (name, url, events, active, auth_header_name, auth_header_enc, auth_header_hint,
                                  secret_enc, timeout_seconds, max_attempts, created_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning {HOOK_COLUMNS}""",
            (body.name.strip(), url, events, body.active, header_name,
             secrets_box.encrypt(header, "webhook-auth") if header else None, _hint(header) if header else None,
             secrets_box.encrypt(secret, "webhook-secret"), body.timeout_seconds, body.max_attempts, admin.user_id))
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="webhooks", row_id=row["id"],
                    after={"name": row["name"], "url": url, "events": events, "auth_header": header_name,
                           "active": body.active})
    return {**row, "secret": secret}  # shown once


class WebhookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    events: list[str] | None = Field(default=None, min_length=1, max_length=50)
    active: bool | None = None
    auth_header_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")
    auth_header_value: str | None = Field(default=None, max_length=2000)  # "" removes the header, None keeps it
    timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    max_attempts: int | None = Field(default=None, ge=1, le=20)


@router.patch("/webhooks/{webhook_id}")
async def update_webhook(webhook_id: int, body: WebhookPatch, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        before = await db.fetch_one(conn, f"select {HOOK_COLUMNS} from webhooks where id = %s for update", (webhook_id,))
        if before is None:
            raise HTTPException(404, "Webhooken findes ikke")
        after = {
            "name": body.name.strip() if body.name else before["name"],
            "url": _url(body.url) if body.url else before["url"],
            "events": _events(body.events) if body.events is not None else before["events"],
            "active": before["active"] if body.active is None else body.active,
            "timeout_seconds": body.timeout_seconds or before["timeout_seconds"],
            "max_attempts": body.max_attempts or before["max_attempts"],
            "auth_header_name": body.auth_header_name or before["auth_header_name"],
        }
        await conn.execute("""update webhooks set name = %s, url = %s, events = %s, active = %s, timeout_seconds = %s,
                                     max_attempts = %s, auth_header_name = %s, updated_at = now(),
                                     disabled_reason = case when %s then null else disabled_reason end,
                                     consecutive_failures = case when %s then 0 else consecutive_failures end
                              where id = %s""",
                           (after["name"], after["url"], after["events"], after["active"], after["timeout_seconds"],
                            after["max_attempts"], after["auth_header_name"], after["active"], after["active"],
                            webhook_id))
        b, a = changed(before, after)
        if body.auth_header_value is not None:
            header = body.auth_header_value.strip()
            name = after["auth_header_name"] or "Authorization"
            await conn.execute("""update webhooks set auth_header_name = %s, auth_header_enc = %s, auth_header_hint = %s
                                  where id = %s""",
                               (name if header else None, secrets_box.encrypt(header, "webhook-auth") if header else None,
                                _hint(header) if header else None, webhook_id))
            a["auth_header"] = "changed" if header else "removed"  # never the value
        if a:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="webhooks", row_id=webhook_id,
                        before=b, after=a)
        return await db.fetch_one(conn, f"select {HOOK_COLUMNS} from webhooks where id = %s", (webhook_id,))


@router.post("/webhooks/{webhook_id}/rotate-secret")
async def rotate_secret(webhook_id: int, admin: Principal = Depends(require_admin_stepup)):
    secret = webhooks.new_secret()
    async with db.connection() as conn:
        cur = await conn.execute("update webhooks set secret_enc = %s, updated_at = now() where id = %s",
                                 (secrets_box.encrypt(secret, "webhook-secret"), webhook_id))
        if not cur.rowcount:
            raise HTTPException(404, "Webhooken findes ikke")
        await audit(conn, "webhook.secret_rotated", actor_id=admin.user_id, meta=admin.meta, table="webhooks",
                    row_id=webhook_id)
    return {"secret": secret}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from webhooks where id = %s returning name, url", (webhook_id,))
        if row is None:
            raise HTTPException(404, "Webhooken findes ikke")
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="webhooks", row_id=webhook_id,
                    before=row)
    return {"ok": True}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: int, admin: Principal = Depends(require_admin)):
    """Sends webhook.ping now and answers with the result. A test is not retried."""
    async with db.connection() as conn:
        if not await db.fetch_one(conn, "select 1 from webhooks where id = %s", (webhook_id,)):
            raise HTTPException(404, "Webhooken findes ikke")
        delivery_id = await webhooks.delivery_for(conn, webhook_id, "webhook.ping",
                                                  {"message": "Test fra /admin/webhooks", "by_user_id": admin.user_id})
        await audit(conn, "webhook.test", actor_id=admin.user_id, meta=admin.meta, table="webhooks", row_id=webhook_id)
    result = await webhooks.attempt(str(delivery_id))
    if not result["ok"]:
        async with db.connection() as conn:
            await conn.execute("update webhook_deliveries set status = 'dead' where id = %s", (delivery_id,))
    return {"delivery_id": delivery_id, **result}


@router.get("/webhooks/{webhook_id}/deliveries")
async def list_deliveries(webhook_id: int, status: str | None = None, limit: int = 50):
    where, params = "webhook_id = %s", [webhook_id]
    if status:
        where += " and status = %s"
        params.append(status)
    async with db.connection() as conn:
        return await db.fetch_all(conn, f"""
            select id, event, status, attempts, last_status, last_error, last_response, duration_ms, payload,
                   created_at, last_attempt_at, delivered_at
            from webhook_deliveries where {where} order by created_at desc limit %s""",
            params + [max(1, min(limit, 200))])


@router.post("/webhooks/deliveries/{delivery_id}/redeliver")
async def redeliver(delivery_id: uuid.UUID, admin: Principal = Depends(require_admin)):
    """Sends the same delivery again (same X-Gaer-Delivery id, so the receiver can tell it is a repeat)."""
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            update webhook_deliveries d set status = 'pending', attempts = 0, last_error = null
            from webhooks w where d.id = %s and w.id = d.webhook_id returning d.webhook_id, w.max_attempts""",
            (delivery_id,))
        if row is None:
            raise HTTPException(404, "Leveringen findes ikke")
        job_id = await jobs.enqueue(conn, "webhook.deliver", {"delivery_id": str(delivery_id)},
                                    max_attempts=row["max_attempts"], dedupe_key=f"webhook:{delivery_id}")
        await audit(conn, "webhook.redeliver", actor_id=admin.user_id, meta=admin.meta, table="webhook_deliveries",
                    row_id=delivery_id)
    return {"ok": True, "job_id": job_id}


# ── Incoming webhooks ──────────────────────────────────────────────────────

IN_COLUMNS = ("id, name, key, verify, header_name, secret_hint, handler, event_header, event_field, id_header, "
              "allowed_ips::text[] as allowed_ips, max_body_kb, max_per_minute, tolerance_seconds, active, "
              "received_count, rejected_count, last_received_at, last_rejected_at, last_rejected_reason, created_at, "
              "updated_at")
HEADER = r"^[A-Za-z0-9-]{1,64}$"


def _in_url(key: str) -> str:
    return f"{config.settings.origin.rstrip('/')}/api/hooks/in/{key}"


def _with_url(row: dict) -> dict:
    return {**row, "url": _in_url(row["key"])}


def _nets(values: list[str]) -> list[str]:
    import ipaddress
    out = []
    for v in values:
        try:
            out.append(str(ipaddress.ip_network(v.strip(), strict=False)))
        except ValueError:
            raise HTTPException(400, f"Ugyldigt IP-interval: {v}")
    return out


def _handler(name: str) -> str:
    if name not in webhooks_in.HANDLERS:
        raise HTTPException(400, "Ukendt handler")
    return name


def _new_secret(verify: str, given: str | None) -> str | None:
    """gaer/hmac: a generated signing secret unless one is given (the sender's). token: the given header value or a
    generated "Bearer …". none: no secret."""
    if verify == "none":
        return None
    if given and given.strip():
        return given.strip()
    return ("Bearer " + webhooks.new_secret()[6:]) if verify == "token" else webhooks.new_secret()


@router.get("/inbound-webhooks")
async def list_inbound():
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"select {IN_COLUMNS} from webhook_endpoints order by created_at")
    return {"items": [_with_url(i) for i in items],
            "handlers": [{"name": k, "description": v.description} for k, v in webhooks_in.HANDLERS.items()]}


class InboundIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    verify: str = Field(default="gaer", pattern="^(gaer|hmac|token|none)$")
    header_name: str | None = Field(default=None, pattern=HEADER)
    secret: str | None = Field(default=None, max_length=2000)  # the sender's secret/token; empty = we make one
    handler: str = Field(default="store", max_length=60)
    event_header: str | None = Field(default=None, pattern=HEADER)
    event_field: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.]{1,64}$")
    id_header: str | None = Field(default=None, pattern=HEADER)
    allowed_ips: list[str] = Field(default_factory=list, max_length=50)
    max_body_kb: int = Field(default=256, ge=1, le=5120)
    max_per_minute: int = Field(default=120, ge=1, le=10000)
    tolerance_seconds: int = Field(default=300, ge=30, le=3600)
    active: bool = True


@router.post("/inbound-webhooks", status_code=201)
async def create_inbound(body: InboundIn, admin: Principal = Depends(require_admin_stepup)):
    secret = _new_secret(body.verify, body.secret)
    header = body.header_name or {"token": "Authorization", "hmac": "X-Signature-256"}.get(body.verify)
    async with db.connection() as conn:
        row = await db.fetch_one(conn, f"""
            insert into webhook_endpoints (name, key, verify, header_name, secret_enc, secret_hint, handler, event_header,
                                           event_field, id_header, allowed_ips, max_body_kb, max_per_minute,
                                           tolerance_seconds, active, created_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::cidr[], %s, %s, %s, %s, %s) returning {IN_COLUMNS}""",
            (body.name.strip(), webhooks_in.new_key(), body.verify, header,
             secrets_box.encrypt(secret, "webhook-in") if secret else None, _hint(secret) if secret else None,
             _handler(body.handler), body.event_header, body.event_field, body.id_header, _nets(body.allowed_ips),
             body.max_body_kb, body.max_per_minute, body.tolerance_seconds, body.active, admin.user_id))
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="webhook_endpoints", row_id=row["id"],
                    after={"name": row["name"], "verify": body.verify, "handler": body.handler,
                           "allowed_ips": row["allowed_ips"]})
    return {**_with_url(row), "secret": secret}  # shown once


class InboundPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    verify: str | None = Field(default=None, pattern="^(gaer|hmac|token|none)$")
    header_name: str | None = Field(default=None, pattern=HEADER)
    secret: str | None = Field(default=None, max_length=2000)  # set the sender's secret/token
    handler: str | None = Field(default=None, max_length=60)
    event_header: str | None = Field(default=None, max_length=64)   # "" clears
    event_field: str | None = Field(default=None, max_length=64)
    id_header: str | None = Field(default=None, max_length=64)
    allowed_ips: list[str] | None = Field(default=None, max_length=50)
    max_body_kb: int | None = Field(default=None, ge=1, le=5120)
    max_per_minute: int | None = Field(default=None, ge=1, le=10000)
    tolerance_seconds: int | None = Field(default=None, ge=30, le=3600)
    active: bool | None = None


@router.patch("/inbound-webhooks/{endpoint_id}")
async def update_inbound(endpoint_id: int, body: InboundPatch, admin: Principal = Depends(require_admin_stepup)):
    import re
    async with db.connection() as conn:
        before = await db.fetch_one(conn, f"select {IN_COLUMNS}, secret_enc is not null as has_secret "
                                          "from webhook_endpoints where id = %s for update", (endpoint_id,))
        if before is None:
            raise HTTPException(404, "Endpointet findes ikke")
        fields = body.model_dump(exclude_unset=True)
        secret = fields.pop("secret", None)
        after = {}
        for k, v in fields.items():
            if k in ("event_header", "id_header", "event_field"):
                v = v or None
                pattern = r"^[A-Za-z0-9_.]{1,64}$" if k == "event_field" else HEADER
                if v is not None and not re.match(pattern, v):
                    raise HTTPException(400, f"Ugyldigt felt: {k}")
            if k == "handler":
                v = _handler(v)
            if k == "allowed_ips":
                v = _nets(v)
            if v is not None or k in ("event_header", "id_header", "event_field"):
                after[k] = v
        verify = after.get("verify", before["verify"])
        if verify in ("gaer", "hmac", "token") and not before["has_secret"] and not secret:
            secret = _new_secret(verify, None)
        if after:
            sets = ", ".join(f"{k} = %s" + ("::cidr[]" if k == "allowed_ips" else "") for k in after)
            await conn.execute(f"update webhook_endpoints set {sets}, updated_at = now() where id = %s",
                               (*after.values(), endpoint_id))
        b, a = changed({k: before[k] for k in after}, after)
        if secret:
            await conn.execute("update webhook_endpoints set secret_enc = %s, secret_hint = %s where id = %s",
                               (secrets_box.encrypt(secret, "webhook-in"), _hint(secret), endpoint_id))
            a["secret"] = "changed"  # never the value
        if a:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="webhook_endpoints",
                        row_id=endpoint_id, before=b, after=a)
        row = await db.fetch_one(conn, f"select {IN_COLUMNS} from webhook_endpoints where id = %s", (endpoint_id,))
    out = _with_url(row)
    if secret and not body.secret:
        out["secret"] = secret  # generated now, shown once
    return out


@router.post("/inbound-webhooks/{endpoint_id}/rotate")
async def rotate_inbound(endpoint_id: int, what: str = "secret", admin: Principal = Depends(require_admin_stepup)):
    """what=secret: a new signing secret/token. what=key: a new URL (the old one stops working at once)."""
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select verify from webhook_endpoints where id = %s for update", (endpoint_id,))
        if row is None:
            raise HTTPException(404, "Endpointet findes ikke")
        if what == "key":
            key = webhooks_in.new_key()
            await conn.execute("update webhook_endpoints set key = %s, updated_at = now() where id = %s", (key, endpoint_id))
            out = {"url": _in_url(key)}
        elif what == "secret" and row["verify"] != "none":
            secret = _new_secret(row["verify"], None)
            await conn.execute("""update webhook_endpoints set secret_enc = %s, secret_hint = %s, updated_at = now()
                                  where id = %s""", (secrets_box.encrypt(secret, "webhook-in"), _hint(secret), endpoint_id))
            out = {"secret": secret}
        else:
            raise HTTPException(400, "Kan ikke skiftes")
        await audit(conn, f"webhook.{what}_rotated", actor_id=admin.user_id, meta=admin.meta, table="webhook_endpoints",
                    row_id=endpoint_id)
    return out


@router.delete("/inbound-webhooks/{endpoint_id}")
async def delete_inbound(endpoint_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from webhook_endpoints where id = %s returning name, received_count",
                                 (endpoint_id,))
        if row is None:
            raise HTTPException(404, "Endpointet findes ikke")
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="webhook_endpoints",
                    row_id=endpoint_id, before=row)
    return {"ok": True}


@router.get("/inbound-webhooks/{endpoint_id}/events")
async def list_inbound_events(endpoint_id: int, status: str | None = None, limit: int = 50):
    where, params = "endpoint_id = %s", [endpoint_id]
    if status:
        where += " and status = %s"
        params.append(status)
    async with db.connection() as conn:
        return await db.fetch_all(conn, f"""
            select id, external_id, event, status, content_type, headers, left(body, 20000) as body, body_json,
                   host(ip) as ip, attempts, result, error, received_at, processed_at
            from webhook_inbound where {where} order by received_at desc limit %s""",
            params + [max(1, min(limit, 200))])


@router.post("/inbound-webhooks/events/{event_id}/replay")
async def replay_inbound(event_id: uuid.UUID, admin: Principal = Depends(require_admin)):
    """Runs the handler again (after a fix in the code, or for a failed event)."""
    async with db.connection() as conn:
        cur = await conn.execute("""update webhook_inbound set status = 'received', error = null, processed_at = null
                                    where id = %s""", (event_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Hændelsen findes ikke")
        job_id = await jobs.enqueue(conn, "webhook.inbound", {"id": str(event_id)}, max_attempts=5,
                                    dedupe_key=f"inbound:{event_id}")
        await audit(conn, "webhook.replay", actor_id=admin.user_id, meta=admin.meta, table="webhook_inbound",
                    row_id=event_id)
    return {"ok": True, "job_id": job_id}


@router.delete("/inbound-webhooks/events/{event_id}")
async def delete_inbound_event(event_id: uuid.UUID, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from webhook_inbound where id = %s returning endpoint_id, event",
                                 (event_id,))
        if row is None:
            raise HTTPException(404, "Hændelsen findes ikke")
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="webhook_inbound", row_id=event_id,
                    before=row)
    return {"ok": True}


# ── Jobs ────────────────────────────────────────────────────────────────────

JOB_STATUS = ("queued", "running", "succeeded", "failed", "dead")


@router.get("/jobs")
async def list_jobs(status: str | None = None, kind: str | None = None, limit: int = 100):
    where, params = ["true"], []
    if status:
        where.append("status = %s")
        params.append(status)
    if kind:
        where.append("kind = %s")
        params.append(kind)
    async with db.connection() as conn:
        items = await db.fetch_all(conn, f"""
            select id, kind, payload, status, priority, run_at, attempts, max_attempts, locked_by, last_error, result,
                   schedule, created_at, started_at, finished_at
            from jobs where {' and '.join(where)} order by id desc limit %s""", params + [max(1, min(limit, 500))])
        counts = await db.fetch_all(conn, "select status, count(*) as n from jobs group by status")
        kinds = await db.fetch_all(conn, "select distinct kind from jobs order by kind")
    return {"items": items, "counts": {c["status"]: c["n"] for c in counts}, "kinds": [k["kind"] for k in kinds],
            "handlers": sorted(jobs.HANDLERS)}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            update jobs set status = 'queued', attempts = 0, run_at = now(), finished_at = null
            where id = %s and status in ('dead', 'failed') returning kind""", (job_id,))
        if row is None:
            raise HTTPException(409, "Kun opgivne jobs kan prøves igen")
        await audit(conn, "job.retry", actor_id=admin.user_id, meta=admin.meta, table="jobs", row_id=job_id,
                    kind=row["kind"])
    return {"ok": True}


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from jobs where id = %s and status in ('queued', 'dead', 'failed') "
                                       "returning kind, status", (job_id,))
        if row is None:
            raise HTTPException(409, "Kun ventende eller opgivne jobs kan slettes")
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="jobs", row_id=job_id, before=row)
    return {"ok": True}


class JobIn(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    payload: dict = Field(default_factory=dict)


@router.post("/jobs", status_code=201)
async def add_job(body: JobIn, admin: Principal = Depends(require_admin)):
    """Starts a job by hand (only kinds the code has a handler for)."""
    if body.kind not in jobs.HANDLERS or body.kind == "webhook.deliver":
        raise HTTPException(400, "Den type job kan ikke startes herfra")
    async with db.connection() as conn:
        job_id = await jobs.enqueue(conn, body.kind, body.payload, max_attempts=3)
        await audit(conn, "job.enqueued", actor_id=admin.user_id, meta=admin.meta, table="jobs", row_id=job_id,
                    kind=body.kind)
    return {"id": job_id}


@router.get("/jobs/schedules")
async def list_schedules():
    async with db.connection() as conn:
        return await db.fetch_all(conn, """
            select s.*, j.status as last_status, j.last_error, j.finished_at as last_finished_at
            from job_schedules s left join jobs j on j.id = s.last_job_id order by s.name""")


class ScheduleIn(BaseModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=30 * 86400)


@router.patch("/jobs/schedules/{name}")
async def update_schedule(name: str, body: ScheduleIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await db.fetch_one(conn, "select enabled, interval_seconds from job_schedules where name = %s for update",
                                    (name,))
        if before is None:
            raise HTTPException(404, "Planen findes ikke")
        after = {"enabled": before["enabled"] if body.enabled is None else body.enabled,
                 "interval_seconds": body.interval_seconds or before["interval_seconds"]}
        await conn.execute("""update job_schedules set enabled = %s, interval_seconds = %s, updated_at = now(),
                                     next_run_at = least(next_run_at, now() + make_interval(secs => %s))
                              where name = %s""", (after["enabled"], after["interval_seconds"],
                                                   after["interval_seconds"], name))
        b, a = changed(before, after)
        if a:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="job_schedules", row_id=name,
                        before=b, after=a)
    return {"name": name, **after}


@router.post("/jobs/schedules/{name}/run")
async def run_schedule(name: str, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        s = await db.fetch_one(conn, "select * from job_schedules where name = %s for update", (name,))
        if s is None:
            raise HTTPException(404, "Planen findes ikke")
        job_id = await jobs.enqueue(conn, s["kind"], s["payload"], dedupe_key=f"schedule:{name}", schedule=name,
                                    max_attempts=3)
        await conn.execute("update job_schedules set last_run_at = now(), last_job_id = coalesce(%s, last_job_id) "
                           "where name = %s", (job_id, name))
        await audit(conn, "job.enqueued", actor_id=admin.user_id, meta=admin.meta, table="job_schedules", row_id=name,
                    job_id=job_id)
    return {"job_id": job_id, "already_queued": job_id is None}


# ── Retention policies (operational data; the logs are in /admin/retention) ──

@router.get("/retention/policies")
async def list_policies():
    return await retention.policies()


class PolicyIn(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)
    enabled: bool | None = None


@router.patch("/retention/policies/{key}")
async def update_policy(key: str, body: PolicyIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        before = await db.fetch_one(conn, "select days, enabled, min_days from retention_policies where key = %s for update",
                                    (key,))
        if before is None:
            raise HTTPException(404, "Reglen findes ikke")
        if body.days is not None and body.days < before["min_days"]:
            raise HTTPException(400, f"Mindst {before['min_days']} dage")
        after = {"days": body.days or before["days"], "enabled": before["enabled"] if body.enabled is None else body.enabled}
        await conn.execute("update retention_policies set days = %s, enabled = %s, updated_at = now() where key = %s",
                           (after["days"], after["enabled"], key))
        b, a = changed({k: before[k] for k in after}, after)
        if a:
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="retention_policies", row_id=key,
                        before=b, after=a)
    return {"key": key, **after}


@router.post("/retention/policies/run")
async def run_policies(admin: Principal = Depends(require_admin)):
    try:
        return {"deleted": await retention.cleanup(actor_id=admin.user_id, meta=admin.meta)}
    except retention.CleanupErrors as e:
        return {"deleted": e.deleted, "errors": e.errors}
