"""Background work of the mail module (app.jobs) and the Lettermint handler for incoming webhooks.

    mail.provision         create a mailbox's principal in Stalwart (retried until Stalwart answers)
    mail.retention         nightly: expire mail older than the policy (at most 30 days) through Stalwart
    mail.sync              nightly: compare the app's mailboxes with Stalwart's principals, store usage and drift
    mail.backup            nightly: every calendar (.ics) and address book (.vcf) to MAIL_BACKUP_PREFIX
    mail.archive_export    a user's mail as .eml files in a zip in their own home (/Mail-arkiv/), when archive export is on
    mail.principal.delete  remove a principal in Stalwart after GDPR erasure or offboarding

Lettermint posts delivery events to an incoming webhook (/admin/webhooks → Indgående, handler "lettermint"); the
handler stores them in mail_events without the recipient's address (only its domain).
"""

import io
import logging
import re
import zipfile
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from app import config, db, jobs, settings_store, webhooks_in
from app.logs import RequestMeta, audit
from app.mailbox import backend as mb_backend
from app.mailbox import service
from app.mailbox.backend import MailBackendError

log = logging.getLogger("gaer.mailbox")


def _skip() -> dict | None:
    return None if mb_backend.enabled() else {"skipped": "MAILBOX_BACKEND er ikke sat"}


@jobs.handler("mail.provision")
async def provision_job(payload: dict, job: dict) -> dict:
    return _skip() or await service.provision(int(payload["mailbox_id"]))


@jobs.handler("mail.retention")
async def retention_job(payload: dict, job: dict) -> dict:
    if skipped := _skip():
        return skipped
    run = await service.run_retention(dry_run=bool(payload.get("dry_run")), triggered_by=payload.get("triggered_by"))
    return {"run_id": run["id"], "expired": run["expired"], "failures": run["failures"]}


@jobs.handler("mail.principal.delete")
async def delete_principal_job(payload: dict, job: dict) -> dict:
    if skipped := _skip():
        return skipped
    await mb_backend.get_backend().delete_principal(payload["principal"])
    return {"deleted": payload["principal"]}


@jobs.handler("mail.sync")
async def sync_job(payload: dict, job: dict) -> dict:
    """Reports drift on /admin/mail → Drift: mailboxes that exist in one system only."""
    if skipped := _skip():
        return skipped
    be = mb_backend.get_backend()
    async with db.connection() as conn:
        boxes = await db.fetch_all(conn, service.MAILBOX_SQL + " where m.status in ('active', 'suspended')")
        groups = {r["owner_principal"] for r in await db.fetch_all(
            conn, "select distinct owner_principal from mail_collections where kind = 'shared'")}
    known = {b["stalwart_principal"] for b in boxes} | groups
    drift = []
    for b in boxes:
        try:
            info = await be.principal(b["stalwart_principal"])
        except MailBackendError as e:
            return {"error": str(e)}
        problem = None
        if info is None:
            problem = "Findes kun i appen, ikke i Stalwart"
        elif b["address"] not in (info.get("emails") or []):
            problem = "Adressen mangler på principalen i Stalwart"
        async with db.connection() as conn:
            await conn.execute("""update mailboxes set synced_at = now(), drift = %s,
                                         used_bytes = coalesce(%s, used_bytes) where id = %s""",
                               (problem, (info or {}).get("used_bytes"), b["id"]))
        if problem:
            drift.append({"mailbox_id": b["id"], "address": b["address"], "problem": problem})
    try:
        stalwart_only = [n for n in await be.list_principals() if n not in known and "@" in n]
    except MailBackendError:
        stalwart_only = []
    drift += [{"mailbox_id": None, "address": n, "problem": "Findes kun i Stalwart, ikke i appen"}
              for n in stalwart_only]
    result = {"at": datetime.now(timezone.utc).isoformat(), "mailboxes": len(boxes), "drift": drift[:200]}
    async with db.connection() as conn:
        await settings_store.set_many(conn, {"mail_last_sync": result})
        await audit(conn, "mail.sync", actor_id=payload.get("triggered_by"), meta=RequestMeta(),
                    after={"mailboxes": len(boxes), "drift": len(drift)})
    return {"mailboxes": len(boxes), "drift": len(drift)}


@jobs.handler("mail.backup")
async def backup_job(payload: dict, job: dict) -> dict:
    """Calendars and contacts must survive while mail must not outlive 30 days, so they are backed up separately."""
    if skipped := _skip():
        return skipped
    from app.storage import get_storage

    storage = get_storage()
    async with db.connection() as conn:
        cols = await db.fetch_all(conn, f"select {service.COLLECTION_COLS} from mail_collections c order by c.created_at")
    counts = {"calendars": 0, "address_books": 0, "failures": 0}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for col in cols:
        try:
            data, ctype, _ = await service.export_collection(col)
        except Exception as e:  # one broken collection must not stop the rest
            counts["failures"] += 1
            log.warning("backup of %s failed: %s", col["id"], e)
            continue
        ext = "ics" if col["type"] == "calendar" else "vcf"
        await storage.put(f"{config.settings.mail_backup_prefix}{today}/{col['owner_principal']}/{col['id']}.{ext}",
                          data, ctype)
        counts["calendars" if col["type"] == "calendar" else "address_books"] += 1
    async with db.connection() as conn:
        await settings_store.set_many(conn, {"mail_last_backup": {"at": datetime.now(timezone.utc).isoformat(), **counts}})
    return counts


@jobs.handler("mail.archive_export")
async def archive_export_job(payload: dict, job: dict) -> dict:
    """The user's mail as .eml files in zips of at most MAX_UPLOAD_MB, saved in their home under /Mail-arkiv/."""
    if skipped := _skip():
        return skipped
    from app import filetree
    from app.sessions import Principal

    user_id = int(payload["user_id"])
    be = mb_backend.get_backend()
    async with db.connection() as conn:
        mb = await service.personal_mailbox(conn, user_id)
        user = await db.fetch_one(conn, "select id, email, phone, role, display_name from users where id = %s", (user_id,))
    if mb is None or user is None:
        return {"skipped": "ingen postkasse"}
    ids = await be.messages_before(mb["stalwart_principal"], datetime.now(timezone.utc), "all")
    limit = config.settings.max_upload_mb * 1024 * 1024 - 1024 * 1024
    parts: list[bytes] = []

    def new_zip():
        b = io.BytesIO()
        return b, zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED), 0

    buf, z, size = new_zip()
    count = 0
    for i in range(0, len(ids), 100):
        for mid, raw in await be.raw_messages(mb["stalwart_principal"], ids[i:i + 100]):
            if size and size + len(raw) > limit:
                z.close()
                parts.append(buf.getvalue())
                buf, z, size = new_zip()
            z.writestr(f"{re.sub(r'[^A-Za-z0-9_-]', '_', mid)}.eml", raw)
            size += len(raw)
            count += 1
    z.close()
    parts.append(buf.getvalue())
    p = Principal(session_id=0, user_id=user_id, email=user["email"], phone=user["phone"], role=user["role"],
                  scope="full", display_name=user["display_name"], meta=RequestMeta())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    names = []
    async with db.connection() as conn:
        loc = filetree.parse(f"/users/{user_id}/Mail-arkiv/")
        for n, data in enumerate(parts, 1):
            name = f"mail-{stamp}" + (f"-del{n}" if len(parts) > 1 else "") + ".zip"
            row = await filetree.write(conn, loc, name, data, "application/zip", p, admin=True, via="mail.archive_export")
            names.append(row["filename"])
        await audit(conn, "mail.archive_export", actor_id=user_id, meta=RequestMeta(), table="mailboxes", row_id=mb["id"],
                    after={"messages": count, "files": names})
    return {"messages": count, "files": names}


# ── Lettermint → mail_events ────────────────────────────────────────────────

def _first(data, *paths):
    for path in paths:
        cur = data
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if isinstance(cur, list) and cur:
            cur = cur[0]
        if isinstance(cur, dict):
            cur = cur.get("email") or cur.get("address")
        if isinstance(cur, (str, int)) and str(cur).strip():
            return str(cur).strip()
    return None


def event_type(name: str) -> str | None:
    n = (name or "").lower()
    if "complain" in n or "spam" in n:
        return "complained"
    if "bounce" in n or "fail" in n or "reject" in n:
        return "bounced"
    if "deliver" in n:
        return "delivered"
    if "sent" in n or "queued" in n or "processed" in n or "accepted" in n:
        return "sent"
    return None


@webhooks_in.handler("lettermint", "Lettermint: levering, bounce og klager til mailmodulet (/admin/mail)")
async def on_lettermint(event: dict, endpoint: dict) -> dict:
    body = event.get("body_json") or {}
    if isinstance(body, str):
        import json
        body = json.loads(body)
    items = body if isinstance(body, list) else [body]
    stored = 0
    async with db.connection() as conn:
        for item in items:
            name = event.get("event") or _first(item, "event", "type", "name") or ""
            kind = event_type(name)
            if kind is None:
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else item
            sender = (_first(data, "from", "sender", "message.from", "envelope.from") or "").lower()
            sender = re.sub(r"^.*<([^>]+)>.*$", r"\1", sender)
            recipient = _first(data, "to", "recipient", "email", "message.to", "envelope.to") or ""
            recipient = re.sub(r"^.*<([^>]+)>.*$", r"\1", recipient)
            message_id = _first(data, "message_id", "messageId", "message.id", "id")
            provider_id = _first(item, "id", "event_id", "uuid")
            mb = None
            if sender:
                mb = await db.fetch_one(conn, """
                    select m.id from mailboxes m join mail_domains d on d.id = m.domain_id
                    where m.status <> 'deleted' and m.local_part || '@' || d.name = %s
                    union all select a.mailbox_id from mail_aliases a where a.address = %s limit 1""", (sender, sender))
            row = await db.fetch_one(conn, """
                insert into mail_events (mailbox_id, type, message_id, from_address, recipient_domain, provider_event_id,
                                         detail)
                values (%s, %s, %s, %s, %s, %s, %s) on conflict (provider_event_id) do nothing returning id""",
                (mb["id"] if mb else None, kind, (message_id or "")[:300] or None, sender[:300] or None,
                 recipient.split("@")[-1].lower()[:200] if "@" in recipient else None,
                 f"{endpoint['id']}:{provider_id}"[:300] if provider_id else None,
                 Jsonb({"event": name[:100], "inbound_id": str(event.get("id"))})))
            stored += 1 if row else 0
    return {"stored": stored}
