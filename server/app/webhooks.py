"""Outgoing webhooks: an admin registers an endpoint, the app emits events, the job queue delivers them.

    await webhooks.emit(conn, "review.feedback.created", {"id": 12, "path": "/klip"})   # same transaction as the change

Each event becomes one delivery per matching endpoint and one job (webhook.deliver). A delivery is a POST with JSON:

    {"id": "<delivery uuid>", "event": "…", "created_at": "…", "data": {…}}

and these headers:

    Content-Type: application/json
    User-Agent: Gaer-Webhooks/1
    X-Gaer-Event: <event>
    X-Gaer-Delivery: <delivery uuid>       the same on every retry and on redelivery: dedupe on it
    X-Gaer-Timestamp: <unix seconds>
    X-Gaer-Attempt: <1, 2, …>
    X-Gaer-Signature: t=<timestamp>,v1=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>
    <auth header>: <value>                 optional, e.g. Authorization: Bearer …, set per endpoint

The receiver checks the signature (and that the timestamp is recent) with the signing secret shown once when the
endpoint is created or the secret is rotated. Anything but 2xx is retried with backoff (app.jobs) up to max_attempts;
then the delivery is 'dead'. After FAILURES_BEFORE_DISABLE dead deliveries in a row the endpoint is switched off.
410 Gone switches it off at once. Redirects are not followed.

Payloads carry ids and paths, not personal data: a receiver that needs more fetches it with its own access.
Only https to public addresses, unless WEBHOOK_ALLOW_PRIVATE=1 (development). The address is checked again before
every delivery, so DNS cannot be pointed at an internal service later.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from psycopg.types.json import Jsonb

from app import config, db, jobs, secrets_box
from app.logs import RequestMeta, audit

USER_AGENT = "Gaer-Webhooks/1"
FAILURES_BEFORE_DISABLE = 10
RESPONSE_EXCERPT = 2000

# The events the app emits. Keys are what an endpoint subscribes to; "*" subscribes to all.
EVENTS: dict[str, str] = {
    "webhook.ping": "Testbesked, når du trykker »Send test«",
    "user.created": "En bruger er oprettet (id, rolle)",
    "user.deleted": "En bruger er slettet efter GDPR (id)",
    "gdpr.request.created": "En GDPR-anmodning er modtaget (id, type)",
    "review.feedback.created": "En review-note er gemt (id, sti, feature, indtryk)",
    "review.session.ended": "En review-session er afsluttet (id, arketype, opgaver)",
    "job.dead": "Et baggrundsjob er opgivet efter alle forsøg (id, type)",
    "ai.budget.warning": "En bruger har passeret en advarselsgrænse for AI-forbrug (bruger-id, budget-id, procent)",
    "ai.budget.cap": "En bruger har nået sit månedlige AI-loft (bruger-id, budget-id, om AI er blokeret)",
    "ai.budget.total": "En bruger har brugt hele sit AI-budget for perioden (bruger-id, budget-id)",
    "mail.mailbox.provisioned": "En postkasse er sat op i Stalwart (postkasse-id, personlig eller fælles)",
    "mail.mailbox.suspended": "En postkasse er suspenderet (postkasse-id)",
    "mail.mailbox.deleted": "En postkasse er slettet eller lavet om til et alias (postkasse-id)",
    "mail.retention.completed": "Sletningen af gammel mail har kørt (kørsel-id, antal slettet, fejl)",
}

# Tests replace this with an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


class UnsafeUrl(ValueError):
    pass


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, header: str, body: bytes, *, tolerance: int = 300) -> bool:
    """What a receiver does (also used by our tests): checks v1 and that the timestamp is recent."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    return abs(time.time() - ts) <= tolerance and hmac.compare_digest(parts.get("v1", ""), sign(secret, ts, body))


def check_url(url: str, *, resolve: bool = False) -> str:
    """Raises UnsafeUrl for anything but https to a public address (http and private hosts when allowed)."""
    allow_private = config.settings.webhook_allow_private
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise UnsafeUrl("Ugyldig adresse")
    if parts.scheme not in (("https", "http") if allow_private else ("https",)):
        raise UnsafeUrl("Adressen skal starte med https://")
    if not parts.hostname or parts.username or parts.password:
        raise UnsafeUrl("Adressen skal have et værtsnavn og må ikke indeholde brugernavn eller kodeord")
    if len(url) > 2000:
        raise UnsafeUrl("Adressen er for lang")
    if allow_private:
        return url.strip()
    host = parts.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise UnsafeUrl("Adressen peger på et internt net")
        addresses = []
        if resolve:
            try:
                infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
            except socket.gaierror:
                raise UnsafeUrl("Værtsnavnet kunne ikke slås op")
            addresses = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    for a in addresses:
        if not a.is_global or a.is_multicast:
            raise UnsafeUrl("Adressen peger på et internt eller reserveret net")
    return url.strip()


def _matches(events: list[str], event: str) -> bool:
    return "*" in events or event in events


async def emit(conn, event: str, data: dict) -> int:
    """Queues a delivery of the event to every active endpoint that subscribes to it. Returns how many."""
    if event not in EVENTS:
        raise ValueError(f"unknown webhook event {event}")
    hooks = await db.fetch_all(conn, "select id, events, max_attempts from webhooks where active")
    n = 0
    for h in hooks:
        if _matches(h["events"], event):
            await _queue(conn, h, event, data)
            n += 1
    return n


async def _queue(conn, hook: dict, event: str, data: dict, *, job: bool = True) -> uuid.UUID:
    delivery_id = uuid.uuid4()
    envelope = {"id": str(delivery_id), "event": event, "created_at": datetime.now(timezone.utc).isoformat(),
                "data": data}
    await conn.execute("insert into webhook_deliveries (id, webhook_id, event, payload) values (%s, %s, %s, %s)",
                       (delivery_id, hook["id"], event, Jsonb(json.loads(json.dumps(envelope, default=str)))))
    if job:
        await jobs.enqueue(conn, "webhook.deliver", {"delivery_id": str(delivery_id)},
                           max_attempts=hook["max_attempts"], dedupe_key=f"webhook:{delivery_id}")
    return delivery_id


async def attempt(delivery_id: str) -> dict:
    """One delivery attempt, recorded on the delivery. Returns {ok, status, error, retry, gone}."""
    async with db.connection() as conn:
        d = await db.fetch_one(conn, """
            select d.*, w.url, w.active, w.auth_header_name, w.auth_header_enc, w.secret_enc, w.timeout_seconds
            from webhook_deliveries d join webhooks w on w.id = d.webhook_id where d.id = %s for update of d""",
            (delivery_id,))
        if d is None:
            return {"ok": False, "error": "Leveringen findes ikke", "retry": False, "gone": False}
        if d["status"] == "succeeded":
            return {"ok": True, "status": d["last_status"], "retry": False, "gone": False}
        attempt_no = d["attempts"] + 1
        await conn.execute("update webhook_deliveries set attempts = %s, last_attempt_at = now() where id = %s",
                           (attempt_no, delivery_id))
    if not d["active"]:
        return await _record(delivery_id, ok=False, error="Webhooken er slået fra", retry=False)
    body = json.dumps(d["payload"], separators=(",", ":"), ensure_ascii=False).encode()
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json", "User-Agent": USER_AGENT, "X-Gaer-Event": d["event"],
        "X-Gaer-Delivery": str(d["id"]), "X-Gaer-Timestamp": str(ts), "X-Gaer-Attempt": str(attempt_no),
        "X-Gaer-Signature": f"t={ts},v1={sign(secrets_box.decrypt(d['secret_enc'], 'webhook-secret'), ts, body)}",
    }
    if d["auth_header_name"] and d["auth_header_enc"]:
        headers[d["auth_header_name"]] = secrets_box.decrypt(d["auth_header_enc"], "webhook-auth")
    started = time.monotonic()
    try:
        await asyncio.to_thread(check_url, d["url"], resolve=True)
        async with httpx.AsyncClient(timeout=d["timeout_seconds"], follow_redirects=False, transport=transport) as client:
            r = await client.post(d["url"], content=body, headers=headers)
    except UnsafeUrl as e:
        return await _record(delivery_id, ok=False, error=str(e), retry=False, started=started)
    except httpx.HTTPError as e:
        return await _record(delivery_id, ok=False, error=f"{type(e).__name__}: {e}"[:500], retry=True, started=started)
    ok = 200 <= r.status_code < 300
    return await _record(delivery_id, ok=ok, status=r.status_code, response=r.text[:RESPONSE_EXCERPT],
                         error=None if ok else f"HTTP {r.status_code}", retry=not ok and r.status_code != 410,
                         gone=r.status_code == 410, started=started)


async def _record(delivery_id: str, *, ok: bool, error: str | None = None, status: int | None = None,
                  response: str | None = None, retry: bool, gone: bool = False, started: float | None = None) -> dict:
    ms = int((time.monotonic() - started) * 1000) if started else None
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            update webhook_deliveries set status = %s, last_status = %s, last_error = %s, last_response = %s,
                   duration_ms = %s, delivered_at = case when %s then now() end
            where id = %s returning webhook_id""",
            ("succeeded" if ok else ("failed" if retry else "dead"), status, error, response, ms, ok, delivery_id))
        if row and ok:
            await conn.execute("""update webhooks set consecutive_failures = 0, last_success_at = now()
                                  where id = %s""", (row["webhook_id"],))
        elif row and not retry:
            await _failed_for_good(conn, row["webhook_id"], gone=gone, reason=error)
    return {"ok": ok, "status": status, "error": error, "retry": retry, "gone": gone}


async def _failed_for_good(conn, webhook_id: int, *, gone: bool, reason: str | None) -> None:
    hook = await db.fetch_one(conn, """
        update webhooks set consecutive_failures = consecutive_failures + 1, last_failure_at = now()
        where id = %s returning consecutive_failures, active""", (webhook_id,))
    if hook and hook["active"] and (gone or hook["consecutive_failures"] >= FAILURES_BEFORE_DISABLE):
        why = "Modtageren svarede 410 Gone" if gone else f"{hook['consecutive_failures']} leveringer i træk fejlede"
        await conn.execute("update webhooks set active = false, disabled_reason = %s, updated_at = now() where id = %s",
                           (why, webhook_id))
        await audit(conn, "webhook.disabled", actor_id=None, meta=RequestMeta(), table="webhooks", row_id=webhook_id,
                    reason=why, last_error=reason)


@jobs.handler("webhook.deliver")
async def deliver(payload: dict, job: dict) -> dict:
    result = await attempt(payload["delivery_id"])
    if result["retry"]:  # after the last attempt the job is dead and _deliver_dead marks the delivery dead
        raise jobs.Retry(result["error"] or "fejlede")
    return result


async def _deliver_dead(conn, job: dict, error: str) -> None:
    row = await db.fetch_one(conn, """update webhook_deliveries set status = 'dead' where id = %s and status <> 'succeeded'
                                      returning webhook_id""", (job["payload"]["delivery_id"],))
    if row:
        await _failed_for_good(conn, row["webhook_id"], gone=False, reason=error)


jobs.DEAD_HOOKS["webhook.deliver"] = _deliver_dead


async def _job_dead(conn, job: dict, error: str) -> None:
    await emit(conn, "job.dead", {"job_id": job["id"], "kind": job["kind"], "error": error[:300]})


# Every other kind that gives up is reported as job.dead (not webhook deliveries: that would loop).
class _DeadHooks(dict):
    def __missing__(self, key):
        return _job_dead

    def __contains__(self, key):
        return True


jobs.DEAD_HOOKS = _DeadHooks(jobs.DEAD_HOOKS)


async def delivery_for(conn, webhook_id: int, event: str, data: dict) -> uuid.UUID:
    """A delivery to one endpoint without a job: the caller runs attempt() itself (the test button)."""
    hook = await db.fetch_one(conn, "select id, events, max_attempts from webhooks where id = %s", (webhook_id,))
    return await _queue(conn, hook, event, data, job=False)
