"""Rules of the mail module (specs/mail): who may see and change which mailbox, calendar and address book, and how
the app's rows are kept in step with Stalwart.

Changes follow one pattern: write the app's rows and the audit entry, then push the result to Stalwart inside the
same transaction. If Stalwart refuses, the transaction rolls back and nothing changed in either system. Stalwart
writes are idempotent, so a retry is always safe. Provisioning is the exception: it runs as the job mail.provision
(retried with backoff), so a mailbox can be requested while Stalwart is down ("Din postkasse bliver sat op").

Admins manage collections, never their content: no function here reads a message, event or contact for display.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import config, db, jobs, settings_store, webhooks
from app.logs import RequestMeta, audit
from app.mailbox import backend as mb_backend
from app.mailbox.backend import ADDRESS_BOOK, CALENDAR, MailBackendError, NotConfigured

SHARED_ROLES = ("reader", "sender", "manager")
SHARED_RANK = {"reader": 1, "sender": 2, "manager": 3}
SHARED_LABEL = {"reader": "Læser", "sender": "Afsender", "manager": "Ansvarlig"}
COLLECTION_ROLES = ("viewer", "editor", "manager")
COLLECTION_RANK = {"viewer": 1, "editor": 2, "manager": 3}
COLLECTION_LABEL = {"viewer": "Se", "editor": "Redigere", "manager": "Ansvarlig", "owner": "Ejer"}
TYPE_LABEL = {CALENDAR: "kalender", ADDRESS_BOOK: "adressebog"}
MAX_RETENTION_DAYS = 30
ADDRESS_RE = re.compile(r"^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9])?@[a-z0-9.-]+\.[a-z]{2,}$")
LOCAL_RE = re.compile(r"^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9])?$")

MAILBOX_SQL = """select m.*, d.name as domain, m.local_part || '@' || d.name as address, d.status as domain_status,
                        u.display_name as owner_name, u.email as owner_email
                 from mailboxes m join mail_domains d on d.id = m.domain_id
                 left join users u on u.id = m.owner_id"""


def require_enabled() -> None:
    if not mb_backend.enabled():
        raise HTTPException(503, "Mail er ikke sat op på denne instans")


def backend():
    try:
        return mb_backend.get_backend()
    except NotConfigured as e:
        raise HTTPException(503, str(e))


def backend_error(e: MailBackendError) -> HTTPException:
    return HTTPException(502, f"Mailserveren svarede med en fejl: {e}")


def clean_address(value: str) -> str:
    address = (value or "").strip().lower()
    if not ADDRESS_RE.match(address):
        raise HTTPException(400, "Ugyldig mailadresse")
    return address


def clean_local(value: str) -> str:
    local = (value or "").strip().lower()
    if not LOCAL_RE.match(local):
        raise HTTPException(400, "Ugyldig adresse før @ (brug a-z, 0-9, punktum, bindestreg)")
    return local


def require_reason(reason: str | None) -> str:
    """Destructive admin actions ask for a reason of at least a few words."""
    text = (reason or "").strip()
    if len(text) < 10 or len(text.split()) < 2:
        raise HTTPException(400, "Skriv en begrundelse på mindst et par ord")
    return text[:500]


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")).strip("-")
    return f"{base[:40] or 'samling'}-{secrets.token_hex(2)}"


# ── Mailboxes and addresses ─────────────────────────────────────────────────

async def personal_mailbox(conn, user_id: int) -> dict | None:
    return await db.fetch_one(conn, MAILBOX_SQL + " where m.owner_id = %s and m.kind = 'personal' "
                                                  "and m.status <> 'deleted'", (user_id,))


async def mailbox(conn, mailbox_id: int, *, kind: str | None = None, lock: bool = False) -> dict:
    row = await db.fetch_one(conn, MAILBOX_SQL + " where m.id = %s and m.status <> 'deleted'"
                             + (" and m.kind = %s" if kind else "") + (" for update of m" if lock else ""),
                             (mailbox_id, kind) if kind else (mailbox_id,))
    if row is None:
        raise HTTPException(404, "Postkassen findes ikke")
    return row


async def aliases(conn, mailbox_id: int) -> list[dict]:
    return await db.fetch_all(conn, "select id, address, created_at from mail_aliases where mailbox_id = %s "
                                    "order by address", (mailbox_id,))


async def domain(conn, domain_id: int) -> dict:
    row = await db.fetch_one(conn, "select * from mail_domains where id = %s", (domain_id,))
    if row is None:
        raise HTTPException(404, "Domænet findes ikke")
    return row


async def domain_for_address(conn, address: str) -> dict:
    row = await db.fetch_one(conn, "select * from mail_domains where name = %s", (address.split("@", 1)[1],))
    if row is None:
        raise HTTPException(400, "Adressen skal være på et af instansens maildomæner")
    if row["status"] == "suspended":
        raise HTTPException(409, "Domænet er suspenderet")
    return row


async def ensure_free(conn, address: str) -> None:
    taken = await db.fetch_one(conn, """
        select 1 from mailboxes m join mail_domains d on d.id = m.domain_id
        where m.status <> 'deleted' and m.local_part || '@' || d.name = %s
        union all select 1 from mail_aliases where address = %s""", (address, address))
    if taken:
        raise HTTPException(409, "Adressen er allerede i brug")


async def principal_emails(conn, mb: dict) -> list[str]:
    return [mb["address"]] + [a["address"] for a in await aliases(conn, mb["id"])]


async def push_mailbox(conn, mb: dict) -> None:
    """Sends the mailbox's addresses, quota and status to Stalwart (idempotent)."""
    if mb["status"] == "provisioning":
        return  # mail.provision sends everything once the principal exists
    emails = await principal_emails(conn, mb)
    try:
        await backend().update_principal(mb["stalwart_principal"], emails=emails, quota_mb=mb["quota_mb"],
                                         enabled=mb["status"] == "active")
    except MailBackendError as e:
        raise backend_error(e)


async def member_principals(conn, mailbox_id: int) -> dict[str, str]:
    rows = await db.fetch_all(conn, """
        select pm.stalwart_principal, s.role from mail_shared_members s
        join mailboxes pm on pm.owner_id = s.user_id and pm.kind = 'personal' and pm.status in ('active', 'suspended')
        where s.mailbox_id = %s""", (mailbox_id,))
    return {r["stalwart_principal"]: r["role"] for r in rows}


async def push_members(conn, mb: dict) -> None:
    if mb["status"] == "provisioning":
        return  # mail.provision sends the members once the group exists
    try:
        await backend().set_group_members(mb["stalwart_principal"], await member_principals(conn, mb["id"]))
    except MailBackendError as e:
        raise backend_error(e)


async def request_mailbox(conn, *, kind: str, domain_row: dict, local_part: str, actor_id: int | None,
                          meta: RequestMeta, owner_id: int | None = None, display_name: str | None = None,
                          quota_mb: int | None = None) -> dict:
    """Creates the app's row and queues mail.provision. The principal name is the address (the client login)."""
    if domain_row["status"] == "suspended":
        raise HTTPException(409, "Domænet er suspenderet")
    local_part = clean_local(local_part)
    address = f"{local_part}@{domain_row['name']}"
    await ensure_free(conn, address)
    if owner_id is not None and await personal_mailbox(conn, owner_id):
        raise HTTPException(409, "Brugeren har allerede en postkasse")
    row = await db.fetch_one(conn, """
        insert into mailboxes (domain_id, local_part, kind, owner_id, display_name, stalwart_principal, quota_mb,
                               created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (domain_row["id"], local_part, kind, owner_id, display_name, address,
         quota_mb or config.settings.mail_default_quota_mb, actor_id))
    await audit(conn, "mail.mailbox.create", actor_id=actor_id, meta=meta, table="mailboxes", row_id=row["id"],
                after={"address": address, "kind": kind, "owner_id": owner_id,
                       "quota_mb": quota_mb or config.settings.mail_default_quota_mb})
    await jobs.enqueue(conn, "mail.provision", {"mailbox_id": row["id"]}, dedupe_key=f"mail.provision:{row['id']}",
                       max_attempts=10)
    return await mailbox(conn, row["id"])


async def default_local_part(conn, user: dict, domain_row: dict) -> str:
    """firstname.lastname from the display name, else the part before @ of the login e-mail; a number if taken."""
    name = (user.get("display_name") or "").strip().lower()
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("é", "e"), ("ü", "u"), ("ö", "oe"), ("ä", "ae")):
        name = name.replace(a, b)
    base = re.sub(r"[^a-z0-9]+", ".", name).strip(".") or (user.get("email") or "").split("@")[0].lower()
    base = re.sub(r"[^a-z0-9._-]+", "", base).strip("._-")[:50] or f"bruger{user['id']}"
    for i in range(0, 100):
        candidate = base if i == 0 else f"{base}{i + 1}"
        address = f"{candidate}@{domain_row['name']}"
        taken = await db.fetch_one(conn, """
            select 1 from mailboxes m where m.domain_id = %s and m.local_part = %s and m.status <> 'deleted'
            union all select 1 from mail_aliases where address = %s""", (domain_row["id"], candidate, address))
        if not taken:
            return candidate
    raise HTTPException(409, "Kunne ikke finde en ledig adresse")


async def provision(mailbox_id: int) -> dict:
    """Creates the principal in Stalwart and, for a personal mailbox, the default calendar, address book and
    identity. Idempotent; the job mail.provision retries it until Stalwart answers."""
    be = mb_backend.get_backend()
    async with db.connection() as conn:
        mb = await db.fetch_one(conn, MAILBOX_SQL + " where m.id = %s", (mailbox_id,))
        if mb is None or mb["status"] == "deleted":
            return {"skipped": "gone"}
        emails = await principal_emails(conn, mb)
    try:
        if mb["kind"] == "personal":
            await be.ensure_account(mb["stalwart_principal"], emails=emails, quota_mb=mb["quota_mb"],
                                    description=mb["owner_name"] or mb["address"])
        else:
            await be.ensure_group(mb["stalwart_principal"], emails=emails, description=mb["display_name"] or mb["address"])
        if mb["status"] == "suspended":
            await be.update_principal(mb["stalwart_principal"], enabled=False)
    except MailBackendError as e:
        async with db.connection() as conn:
            await conn.execute("update mailboxes set provision_error = %s, updated_at = now() where id = %s",
                               (str(e)[:500], mailbox_id))
        raise
    async with db.connection() as conn:
        mb = await mailbox(conn, mailbox_id, lock=True)
        first = mb["status"] == "provisioning"
        if first:
            await conn.execute("""update mailboxes set status = 'active', provisioned_at = now(), synced_at = now(),
                                         provision_error = null, drift = null, updated_at = now() where id = %s""",
                               (mailbox_id,))
            mb = await mailbox(conn, mailbox_id)
        meta = RequestMeta()
        if mb["kind"] == "personal":
            for type, name in ((CALENDAR, "Kalender"), (ADDRESS_BOOK, "Kontakter")):
                have = await db.fetch_one(conn, "select id from mail_collections where owner_id = %s and type = %s "
                                                "and is_default", (mb["owner_id"], type))
                if have is None:
                    await create_collection(conn, type=type, kind="personal", owner_id=mb["owner_id"],
                                            owner_principal=mb["stalwart_principal"], name=name, actor_id=None,
                                            meta=meta, is_default=True, slug="default",
                                            colour="#2563eb" if type == CALENDAR else None,
                                            timezone="Europe/Copenhagen" if type == CALENDAR else None)
            await sync_identities(conn, mb["owner_id"])
            # Shared mailboxes the user was added to before their own mailbox existed.
            for s in await db.fetch_all(conn, MAILBOX_SQL + """ where m.id in (select mailbox_id from mail_shared_members
                                                                where user_id = %s) and m.status = 'active'""",
                                        (mb["owner_id"],)):
                await push_members(conn, s)
        else:
            await push_members(conn, mb)
        if first:
            await audit(conn, "mail.mailbox.provisioned", actor_id=None, meta=meta, table="mailboxes", row_id=mailbox_id,
                        after={"address": mb["address"], "principal": mb["stalwart_principal"]})
            await webhooks.emit(conn, "mail.mailbox.provisioned", {"mailbox_id": mailbox_id, "kind": mb["kind"]})
    return {"mailbox_id": mailbox_id, "provisioned": first}


# ── Identities ──────────────────────────────────────────────────────────────

async def allowed_identities(conn, user_id: int) -> list[dict]:
    """(mailbox_id, address) the user may send as: own address and aliases, shared ones as sender or manager."""
    return await db.fetch_all(conn, """
        select m.id as mailbox_id, m.local_part || '@' || d.name as address, 'personal' as kind
        from mailboxes m join mail_domains d on d.id = m.domain_id
        where m.owner_id = %s and m.kind = 'personal' and m.status in ('active', 'suspended')
        union all
        select m.id, a.address, 'alias' from mail_aliases a join mailboxes m on m.id = a.mailbox_id
        where m.owner_id = %s and m.kind = 'personal' and m.status in ('active', 'suspended')
        union all
        select m.id, m.local_part || '@' || d.name, 'shared'
        from mail_shared_members s join mailboxes m on m.id = s.mailbox_id join mail_domains d on d.id = m.domain_id
        where s.user_id = %s and s.role in ('sender', 'manager') and m.status = 'active'
        union all
        select m.id, a.address, 'shared_alias'
        from mail_shared_members s join mailboxes m on m.id = s.mailbox_id join mail_aliases a on a.mailbox_id = m.id
        where s.user_id = %s and s.role in ('sender', 'manager') and m.status = 'active'""",
        (user_id, user_id, user_id, user_id))


async def sync_identities(conn, user_id: int) -> list[dict]:
    """Adds an identity row for each address the user may send as and removes the ones they no longer may."""
    allowed = await allowed_identities(conn, user_id)
    user = await db.fetch_one(conn, "select display_name from users where id = %s", (user_id,))
    for a in allowed:
        await conn.execute("""insert into mail_identities (user_id, mailbox_id, from_address, display_name)
                              values (%s, %s, %s, %s) on conflict (user_id, from_address) do nothing""",
                           (user_id, a["mailbox_id"], a["address"], (user or {}).get("display_name") or ""))
    await conn.execute("delete from mail_identities where user_id = %s and not (from_address = any(%s))",
                       (user_id, [a["address"] for a in allowed]))
    kinds = {a["address"]: a["kind"] for a in allowed}
    rows = await db.fetch_all(conn, "select id, mailbox_id, from_address, display_name, signature, updated_at "
                                    "from mail_identities where user_id = %s order by from_address", (user_id,))
    return [{**r, "kind": kinds.get(r["from_address"])} for r in rows]


# ── Calendars and address books ─────────────────────────────────────────────

COLLECTION_COLS = """c.id, c.type, c.kind, c.owner_id, c.owner_principal, c.slug, c.name, c.colour, c.timezone,
                     c.is_default, c.freebusy, c.entry_count, c.created_at, c.updated_at"""


async def create_collection(conn, *, type: str, kind: str, owner_id: int | None, owner_principal: str, name: str,
                            actor_id: int | None, meta: RequestMeta, colour: str | None = None,
                            timezone: str | None = None, is_default: bool = False, slug: str | None = None) -> dict:
    slug = slug or slugify(name)
    try:
        await backend().ensure_collection(owner_principal, type, slug, name=name, colour=colour, timezone=timezone)
    except MailBackendError as e:
        raise backend_error(e)
    row = await db.fetch_one(conn, f"""
        insert into mail_collections as c (type, kind, owner_id, owner_principal, slug, name, colour, timezone, is_default,
                                        entry_count, created_by)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s) returning {COLLECTION_COLS}""",
        (type, kind, owner_id, owner_principal, slug, name, colour, timezone, is_default, actor_id))
    await audit(conn, "mail.collection.create", actor_id=actor_id, meta=meta, table="mail_collections", row_id=row["id"],
                after={"type": type, "kind": kind, "name": name, "owner_id": owner_id})
    return row


async def collection(conn, collection_id, *, lock: bool = False) -> dict:
    try:
        row = await db.fetch_one(conn, f"select {COLLECTION_COLS} from mail_collections c where c.id = %s"
                                 + (" for update" if lock else ""), (str(collection_id),))
    except Exception:
        row = None
    if row is None:
        raise HTTPException(404, "Kalenderen eller adressebogen findes ikke")
    return row


async def collection_role(conn, user_id: int, col: dict) -> str | None:
    if col["kind"] == "personal" and col["owner_id"] == user_id:
        return "owner"
    share = await db.fetch_one(conn, "select role from mail_collection_shares where collection_id = %s and user_id = %s",
                               (col["id"], user_id))
    return share["role"] if share else None


async def require_collection(conn, user_id: int, collection_id, *, need: str = "viewer", admin: bool = False,
                             lock: bool = False) -> tuple[dict, str]:
    """The collection and the caller's role on it. 404 when the caller has none (ids cannot be probed)."""
    col = await collection(conn, collection_id, lock=lock)
    role = "admin" if admin else await collection_role(conn, user_id, col)
    if role is None:
        raise HTTPException(404, "Kalenderen eller adressebogen findes ikke")
    if role not in ("owner", "admin") and COLLECTION_RANK[role] < COLLECTION_RANK[need]:
        raise HTTPException(403, "Din rolle giver ikke adgang til det")
    return col, role


async def user_principal(conn, user_id: int) -> str:
    mb = await personal_mailbox(conn, user_id)
    if mb is None or mb["status"] == "provisioning":
        raise HTTPException(409, "Personen har ingen postkasse endnu")
    return mb["stalwart_principal"]


async def push_collection_access(conn, col: dict) -> None:
    rows = await db.fetch_all(conn, """
        select m.stalwart_principal, s.role from mail_collection_shares s
        join mailboxes m on m.owner_id = s.user_id and m.kind = 'personal' and m.status in ('active', 'suspended')
        where s.collection_id = %s""", (col["id"],))
    try:
        await backend().set_collection_access(col["owner_principal"], col["type"], col["slug"],
                                              {r["stalwart_principal"]: r["role"] for r in rows})
    except MailBackendError as e:
        raise backend_error(e)


async def update_collection(conn, col: dict, changes: dict, *, actor_id: int, meta: RequestMeta) -> dict:
    after = {**{k: col[k] for k in ("name", "colour", "timezone", "freebusy")}, **changes}
    diff = {k: v for k, v in after.items() if col[k] != v}
    if not diff:
        return col
    try:
        be = backend()
        if diff.keys() & {"name", "colour", "timezone"}:
            await be.ensure_collection(col["owner_principal"], col["type"], col["slug"], name=after["name"],
                                       colour=after["colour"], timezone=after["timezone"])
        if "freebusy" in diff and col["type"] == CALENDAR:
            await be.set_freebusy(col["owner_principal"], col["slug"], after["freebusy"])
    except MailBackendError as e:
        raise backend_error(e)
    row = await db.fetch_one(conn, f"""update mail_collections c set name = %s, colour = %s, timezone = %s, freebusy = %s,
                                       updated_at = now() where c.id = %s returning {COLLECTION_COLS}""",
                             (after["name"], after["colour"], after["timezone"], after["freebusy"], col["id"]))
    await audit(conn, "mail.collection.update", actor_id=actor_id, meta=meta, table="mail_collections", row_id=col["id"],
                before={k: col[k] for k in diff}, after=diff)
    return row


async def delete_collection(conn, col: dict, *, actor_id: int | None, meta: RequestMeta, reason: str | None = None) -> None:
    if col["is_default"] and col["kind"] == "personal":
        raise HTTPException(409, f"Din standard{TYPE_LABEL[col['type']]} kan ikke slettes")
    try:
        await backend().delete_collection(col["owner_principal"], col["type"], col["slug"])
    except MailBackendError as e:
        raise backend_error(e)
    await conn.execute("delete from mail_collections where id = %s", (col["id"],))
    await audit(conn, "mail.collection.delete", actor_id=actor_id, meta=meta, table="mail_collections",
                row_id=col["id"], before={"type": col["type"], "name": col["name"], "owner_id": col["owner_id"]},
                **({"reason": reason} if reason else {}))
    if col["kind"] == "shared":
        try:
            await backend().delete_principal(col["owner_principal"])
        except MailBackendError:
            pass  # an empty group left behind is reported by the nightly sync


async def set_collection_share(conn, col: dict, user_id: int, role: str | None, *, actor_id: int,
                               meta: RequestMeta) -> None:
    """role None removes the share."""
    if col["kind"] == "personal" and col["owner_id"] == user_id:
        raise HTTPException(400, "Ejeren har altid adgang")
    before = await db.fetch_one(conn, "select role from mail_collection_shares where collection_id = %s and user_id = %s",
                                (col["id"], user_id))
    if role is None:
        if before is None:
            raise HTTPException(404, "Personen har ikke adgang")
        await conn.execute("delete from mail_collection_shares where collection_id = %s and user_id = %s",
                           (col["id"], user_id))
    else:
        await user_principal(conn, user_id)
        await conn.execute("""insert into mail_collection_shares (collection_id, user_id, role, added_by)
                              values (%s, %s, %s, %s) on conflict (collection_id, user_id)
                              do update set role = excluded.role""", (col["id"], user_id, role, actor_id))
    if (before or {}).get("role") == role:
        return
    await audit(conn, "mail.collection.share", actor_id=actor_id, meta=meta, table="mail_collection_shares",
                row_id=col["id"], before={"user_id": user_id, "role": before["role"]} if before else None,
                after={"user_id": user_id, "role": role} if role else None, collection=col["name"])
    await push_collection_access(conn, col)


async def shares(conn, collection_id) -> list[dict]:
    return await db.fetch_all(conn, """
        select s.user_id, u.display_name, u.email, s.role, s.added_at from mail_collection_shares s
        join users u on u.id = s.user_id where s.collection_id = %s order by u.display_name, u.email""",
        (collection_id,))


async def collections_for(conn, user_id: int) -> list[dict]:
    """The user's own collections and those shared with them, with their role."""
    rows = await db.fetch_all(conn, f"""
        select {COLLECTION_COLS}, 'owner' as role from mail_collections c where c.owner_id = %s
        union all
        select {COLLECTION_COLS}, s.role from mail_collections c
        join mail_collection_shares s on s.collection_id = c.id where s.user_id = %s
        order by is_default desc, name""", (user_id, user_id))
    for r in rows:
        r["role_label"] = COLLECTION_LABEL[r["role"]]
        r["shares"] = await shares(conn, r["id"]) if r["role"] in ("owner", "manager") else []
    return rows


async def refresh_count(conn, col: dict) -> int | None:
    try:
        n = len(await backend().collection_items(col["owner_principal"], col["type"], col["slug"]))
    except (MailBackendError, HTTPException):
        return col["entry_count"]
    await conn.execute("update mail_collections set entry_count = %s where id = %s", (n, col["id"]))
    return n


async def export_collection(col: dict) -> tuple[bytes, str, str]:
    """(data, content type, filename) — the user's own export, or the nightly backup."""
    try:
        items = await backend().collection_items(col["owner_principal"], col["type"], col["slug"])
    except MailBackendError as e:
        raise backend_error(e)
    safe = re.sub(r"[^\w.-]+", "_", col["name"])[:60] or "samling"
    if col["type"] == CALENDAR:
        return mb_backend.join_calendars(list(items.values()), col["name"]), "text/calendar", f"{safe}.ics"
    body = "".join(v if v.endswith("\n") else v + "\r\n" for v in items.values()).encode()
    return body, "text/vcard", f"{safe}.vcf"


async def transfer_collections(conn, user_id: int, *, actor_id: int, meta: RequestMeta, members: list[int]) -> list[dict]:
    """A leaver's calendars and address books become shared ones (each with its own group principal), with the
    given users as managers. The entries are copied in Stalwart, then the personal collection is removed."""
    be = backend()
    moved = []
    for col in await db.fetch_all(conn, f"select {COLLECTION_COLS} from mail_collections c where c.owner_id = %s "
                                        "order by c.type, c.name", (user_id,)):
        group = f"shared-{col['type'].replace('_', '-')}-{secrets.token_hex(4)}"
        slug = "default"
        try:
            await be.ensure_group(group, emails=[], description=f"Overført: {col['name']}")
            await be.ensure_collection(group, col["type"], slug, name=col["name"], colour=col["colour"],
                                       timezone=col["timezone"])
            items = await be.collection_items(col["owner_principal"], col["type"], col["slug"])
            await be.put_items(group, col["type"], slug, items)
            await be.delete_collection(col["owner_principal"], col["type"], col["slug"])
        except MailBackendError as e:
            raise backend_error(e)
        await conn.execute("""update mail_collections set kind = 'shared', owner_id = null, owner_principal = %s,
                                     slug = %s, is_default = false, entry_count = %s, updated_at = now() where id = %s""",
                           (group, slug, len(items), col["id"]))
        await conn.execute("delete from mail_collection_shares where collection_id = %s and user_id = %s",
                           (col["id"], user_id))
        for m in members:
            await conn.execute("""insert into mail_collection_shares (collection_id, user_id, role, added_by)
                                  values (%s, %s, 'manager', %s) on conflict (collection_id, user_id)
                                  do update set role = 'manager'""", (col["id"], m, actor_id))
        await audit(conn, "mail.collection.transfer", actor_id=actor_id, meta=meta, table="mail_collections",
                    row_id=col["id"], before={"owner_id": user_id}, after={"kind": "shared", "principal": group,
                                                                           "managers": members, "entries": len(items)})
        moved.append({"id": col["id"], "type": col["type"], "name": col["name"], "entries": len(items)})
        await push_collection_access(conn, await collection(conn, col["id"]))
    return moved


# ── Shared mailboxes ────────────────────────────────────────────────────────

async def shared_role(conn, user_id: int, mailbox_id: int) -> str | None:
    row = await db.fetch_one(conn, "select role from mail_shared_members where mailbox_id = %s and user_id = %s",
                             (mailbox_id, user_id))
    return row["role"] if row else None


async def members(conn, mailbox_id: int) -> list[dict]:
    return await db.fetch_all(conn, """
        select s.user_id, u.display_name, u.email, s.role, s.added_at,
               exists (select 1 from mailboxes pm where pm.owner_id = s.user_id and pm.kind = 'personal'
                       and pm.status = 'active') as has_mailbox
        from mail_shared_members s join users u on u.id = s.user_id
        where s.mailbox_id = %s order by u.display_name, u.email""", (mailbox_id,))


async def set_member(conn, mb: dict, user_id: int, role: str | None, *, actor_id: int, meta: RequestMeta,
                     admin: bool) -> None:
    """role None removes the member. A manager cannot remove the last manager; an admin can."""
    user = await db.fetch_one(conn, "select id from users where id = %s and not impersonation_only", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    before = await shared_role(conn, user_id, mb["id"])
    if role == before:
        return
    if before == "manager" and role != "manager" and not admin:
        managers = await db.fetch_one(conn, "select count(*) as n from mail_shared_members where mailbox_id = %s "
                                            "and role = 'manager'", (mb["id"],))
        if managers["n"] <= 1:
            raise HTTPException(409, "Postkassen skal have mindst én ansvarlig")
    if role is None:
        if before is None:
            raise HTTPException(404, "Personen er ikke medlem")
        await conn.execute("delete from mail_shared_members where mailbox_id = %s and user_id = %s", (mb["id"], user_id))
    else:
        await conn.execute("""insert into mail_shared_members (mailbox_id, user_id, role, added_by) values (%s, %s, %s, %s)
                              on conflict (mailbox_id, user_id) do update set role = excluded.role""",
                           (mb["id"], user_id, role, actor_id))
    await audit(conn, "mail.shared.member", actor_id=actor_id, meta=meta, table="mail_shared_members", row_id=mb["id"],
                before={"user_id": user_id, "role": before} if before else None,
                after={"user_id": user_id, "role": role} if role else None, mailbox=mb["address"])
    await sync_identities(conn, user_id)
    await push_members(conn, mb)


async def memberships(conn, user_id: int) -> list[dict]:
    rows = await db.fetch_all(conn, MAILBOX_SQL.replace("select m.*", "select m.*, s.role") + """
        join mail_shared_members s on s.mailbox_id = m.id
        where s.user_id = %s and m.status <> 'deleted' order by address""", (user_id,))
    return [{"id": r["id"], "address": r["address"], "display_name": r["display_name"], "role": r["role"],
             "role_label": SHARED_LABEL[r["role"]], "status": r["status"]} for r in rows]


# ── App passwords ───────────────────────────────────────────────────────────

async def app_passwords(conn, user_id: int) -> list[dict]:
    return await db.fetch_all(conn, """select id, name, created_at, last_used_at from mail_app_passwords
                                       where user_id = %s and revoked_at is null order by created_at desc""", (user_id,))


async def create_app_password(conn, mb: dict, name: str, *, actor_id: int, meta: RequestMeta) -> dict:
    if mb["status"] != "active":
        raise HTTPException(409, "Postkassen er ikke aktiv")
    n = await db.fetch_one(conn, "select count(*) as n from mail_app_passwords where user_id = %s and revoked_at is null",
                           (mb["owner_id"],))
    if n["n"] >= 20:
        raise HTTPException(409, "Du kan højst have 20 app-adgangskoder. Tilbagekald en, du ikke bruger.")
    secret_name = f"gaer-{mb['owner_id']}-{secrets.token_hex(6)}"
    password = mb_backend.new_app_password()
    try:
        await backend().add_app_password(mb["stalwart_principal"], secret_name, password)
    except MailBackendError as e:
        raise backend_error(e)
    row = await db.fetch_one(conn, """insert into mail_app_passwords (user_id, mailbox_id, name, secret_name)
                                      values (%s, %s, %s, %s) returning id, name, created_at, last_used_at""",
                             (mb["owner_id"], mb["id"], name.strip(), secret_name))
    await audit(conn, "mail.app_password.create", actor_id=actor_id, meta=meta, table="mail_app_passwords",
                row_id=row["id"], after={"name": row["name"], "user_id": mb["owner_id"]})
    return {**row, "password": password, "username": mb["stalwart_principal"]}


async def revoke_app_passwords(conn, user_id: int, *, actor_id: int, meta: RequestMeta, ids: list[int] | None = None,
                               reason: str | None = None) -> int:
    rows = await db.fetch_all(conn, """select p.id, p.name, p.secret_name, m.stalwart_principal from mail_app_passwords p
                                       join mailboxes m on m.id = p.mailbox_id
                                       where p.user_id = %s and p.revoked_at is null"""
                              + (" and p.id = any(%s)" if ids is not None else ""),
                              (user_id, ids) if ids is not None else (user_id,))
    if ids is not None and not rows:
        raise HTTPException(404, "App-adgangskoden findes ikke")
    try:
        for r in rows:
            await backend().remove_app_password(r["stalwart_principal"], r["secret_name"])
    except MailBackendError as e:
        raise backend_error(e)
    for r in rows:
        await conn.execute("update mail_app_passwords set revoked_at = now(), revoked_by = %s where id = %s",
                           (actor_id, r["id"]))
    if rows:
        await audit(conn, "mail.app_password.revoke", actor_id=actor_id, meta=meta, table="mail_app_passwords",
                    row_id=rows[0]["id"] if len(rows) == 1 else None,
                    before={"names": [r["name"] for r in rows], "user_id": user_id},
                    **({"reason": reason} if reason else {}))
    return len(rows)


# ── Retention ───────────────────────────────────────────────────────────────

async def effective_policy(conn, mb: dict) -> dict:
    """Mailbox, then domain, then global; never more than 30 days."""
    row = await db.fetch_one(conn, """
        select scope, days, applies_to from mail_retention_policies
        where (scope = 'mailbox' and mailbox_id = %s) or (scope = 'domain' and domain_id = %s) or scope = 'global'
        order by case scope when 'mailbox' then 0 when 'domain' then 1 else 2 end limit 1""",
        (mb["id"], mb["domain_id"]))
    row = row or {"scope": "global", "days": MAX_RETENTION_DAYS, "applies_to": "all"}
    return {**row, "days": min(row["days"], MAX_RETENTION_DAYS)}


async def run_retention(*, dry_run: bool, triggered_by: int | None, meta: RequestMeta | None = None,
                        mailbox_ids: list[int] | None = None) -> dict:
    """Expires mail through Stalwart (never by a lifecycle rule on its bucket, which also holds events and contacts).
    With archive export on, each message's .eml is copied to MAIL_ARCHIVE_PREFIX first. Returns the run."""
    from app.storage import get_storage

    be = mb_backend.get_backend()
    meta = meta or RequestMeta()
    async with db.connection() as conn:
        run = await db.fetch_one(conn, "insert into mail_retention_runs (dry_run, triggered_by) values (%s, %s) returning id",
                                 (dry_run, triggered_by))
        archive = bool(await settings_store.get(conn, "mail_archive_export"))
        boxes = await db.fetch_all(conn, MAILBOX_SQL + " where m.status in ('active', 'suspended')"
                                   + (" and m.id = any(%s)" if mailbox_ids else "") + " order by m.id",
                                   (mailbox_ids,) if mailbox_ids else None)
        plans = [(mb, await effective_policy(conn, mb)) for mb in boxes]
    now = datetime.now(timezone.utc)
    totals = {"mailboxes": 0, "expired": 0, "archived": 0, "failures": 0}
    per_box = []
    for mb, policy in plans:
        cutoff = now - timedelta(days=policy["days"])
        try:
            ids = await be.messages_before(mb["stalwart_principal"], cutoff, policy["applies_to"])
            archived = expired = 0
            if ids and not dry_run:
                if archive:
                    storage = get_storage()
                    for i in range(0, len(ids), 100):
                        for mid, raw in await be.raw_messages(mb["stalwart_principal"], ids[i:i + 100]):
                            key = f"{config.settings.mail_archive_prefix}{mb['stalwart_principal']}/{now:%Y-%m-%d}/{mid}.eml"
                            await storage.put(key, raw, "message/rfc822")
                            archived += 1
                for i in range(0, len(ids), 500):
                    expired += await be.destroy_messages(mb["stalwart_principal"], ids[i:i + 500])
            elif dry_run:
                expired = len(ids)
            totals["mailboxes"] += 1
            totals["expired"] += expired
            totals["archived"] += archived
            per_box.append({"mailbox_id": mb["id"], "address": mb["address"], "days": policy["days"],
                            "applies_to": policy["applies_to"], "expired": expired})
            if expired and not dry_run:
                async with db.connection() as conn:
                    await conn.execute("""insert into mail_events (mailbox_id, type, count, detail)
                                          values (%s, 'expired', %s, %s)""",
                                       (mb["id"], expired, Jsonb({"run_id": run["id"], "days": policy["days"]})))
        except MailBackendError as e:
            totals["failures"] += 1
            per_box.append({"mailbox_id": mb["id"], "address": mb["address"], "error": str(e)[:300]})
    status = "failed" if totals["failures"] and not totals["mailboxes"] else "succeeded"
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            update mail_retention_runs set status = %s, mailboxes = %s, expired = %s, archived = %s, failures = %s,
                   detail = %s, finished_at = now() where id = %s returning *""",
            (status, totals["mailboxes"], totals["expired"], totals["archived"], totals["failures"],
             Jsonb({"mailboxes": per_box[:500]}), run["id"]))
        await audit(conn, "mail.retention.run", actor_id=triggered_by, meta=meta, table="mail_retention_runs",
                    row_id=run["id"], after={**totals, "dry_run": dry_run, "status": status})
        if not dry_run:
            await webhooks.emit(conn, "mail.retention.completed", {"run_id": run["id"], "expired": totals["expired"],
                                                                   "failures": totals["failures"]})
    return row


async def last_retention_run(conn) -> dict | None:
    return await db.fetch_one(conn, """select id, status, mailboxes, expired, archived, failures, started_at, finished_at
                                       from mail_retention_runs where not dry_run order by id desc limit 1""")
