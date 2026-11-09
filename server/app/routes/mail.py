"""/api/mail: the signed-in user's mailbox, calendars, address books, app passwords and send identities
(/user/mail), and shared mailboxes for their managers. Rules in app/mailbox/service.py.

`<uid>` is "me" or the caller's own id; any other id answers 404 (not 403), so ids cannot be probed. Admins manage
other users under /api/admin/mail (routes/admin_mail.py), where admin roles and the admin audit apply.
Message, event and contact content is never exposed here: clients reach it over IMAP/JMAP, CalDAV and CardDAV.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db, jobs, settings_store
from app.logs import audit
from app.mailbox import backend as mb_backend
from app.mailbox import clients, service
from app.mailbox.backend import ADDRESS_BOOK, CALENDAR, MailBackendError
from app.security import InvalidEmail, normalize_email
from app.sessions import Principal, current_user

router = APIRouter(prefix="/api/mail", tags=["mail"])

COLOUR = r"^#[0-9a-fA-F]{6}$"


def _self(p: Principal, uid: str) -> int:
    if uid == "me" or uid == str(p.user_id):
        return p.user_id
    raise HTTPException(404, "Ikke fundet")


def _real(p: Principal) -> None:
    if p.impersonator_id is not None:
        raise HTTPException(403, "Ikke muligt for en testkonto")


async def _own_mailbox(conn, user_id: int) -> dict:
    mb = await service.personal_mailbox(conn, user_id)
    if mb is None:
        raise HTTPException(404, "Du har ingen postkasse endnu")
    return mb


async def _user_by_email(conn, email: str) -> dict:
    try:
        address = normalize_email(email)
    except InvalidEmail as e:
        raise HTTPException(400, str(e))
    user = await db.fetch_one(conn, "select id, display_name, email from users where email = %s "
                                    "and not impersonation_only and blocked_at is null", (address,))
    if user is None:
        raise HTTPException(404, "Der er ingen bruger med den e-mail")
    return user


async def overview(conn, user_id: int) -> dict:
    """What /user/mail and /admin/mail/users/<id> show. Shared by routes/admin_mail.py."""
    mb = await service.personal_mailbox(conn, user_id)
    out: dict = {"enabled": True, "mailbox": None, "aliases": [], "alias_requests": [], "retention": None,
                 "shared": await service.memberships(conn, user_id), "identities": [], "client": {"configured": False}}
    if mb:
        out["mailbox"] = {k: mb[k] for k in ("id", "address", "kind", "status", "quota_mb", "used_bytes", "requested_at",
                                             "provisioned_at", "last_login_at", "domain")}
        out["aliases"] = await service.aliases(conn, mb["id"])
        out["alias_requests"] = await db.fetch_all(conn, """select id, address, note, status, created_at, decided_at
                                                            from mail_alias_requests where user_id = %s
                                                            order by created_at desc limit 10""", (user_id,))
        out["retention"] = await service.effective_policy(conn, mb)
        out["identities"] = await db.fetch_all(conn, """select id, mailbox_id, from_address, display_name, signature,
                                                               updated_at from mail_identities where user_id = %s
                                                        order by from_address""", (user_id,))
        out["client"] = clients.settings(mb["address"])
    values = await settings_store.get_all(conn)
    out["archive_export"] = bool(values["mail_archive_export"])
    out["last_expiry_run"] = await service.last_retention_run(conn)
    out["managed"] = [s["id"] for s in out["shared"] if s["role"] == "manager"]
    return out


@router.get("/status")
async def status(p: Principal = Depends(current_user)):
    """Whether the module is on, for the menu. Says nothing about the server's address."""
    if not mb_backend.enabled():
        return {"enabled": False}
    async with db.connection() as conn:
        mb = await service.personal_mailbox(conn, p.user_id)
    return {"enabled": True, "has_mailbox": mb is not None, "status": mb["status"] if mb else None}


@router.get("/users/{uid}")
async def get_user(uid: str, p: Principal = Depends(current_user)):
    service.require_enabled()
    user_id = _self(p, uid)
    async with db.connection() as conn:
        return await overview(conn, user_id)


class AliasRequestIn(BaseModel):
    address: str = Field(min_length=3, max_length=254)
    note: str = Field(default="", max_length=300)


@router.post("/users/{uid}/alias-requests")
async def request_alias(uid: str, body: AliasRequestIn, p: Principal = Depends(current_user)):
    service.require_enabled()
    _real(p)
    user_id = _self(p, uid)
    address = service.clean_address(body.address)
    async with db.connection() as conn:
        mb = await _own_mailbox(conn, user_id)
        await service.domain_for_address(conn, address)
        await service.ensure_free(conn, address)
        pending = await db.fetch_one(conn, "select 1 from mail_alias_requests where address = %s and status = 'pending'",
                                     (address,))
        if pending:
            raise HTTPException(409, "Der er allerede en anmodning om den adresse")
        row = await db.fetch_one(conn, """insert into mail_alias_requests (user_id, mailbox_id, address, note)
                                          values (%s, %s, %s, %s)
                                          returning id, address, note, status, created_at, decided_at""",
                                 (user_id, mb["id"], address, body.note.strip()))
        await audit(conn, "mail.alias.request", actor_id=p.user_id, meta=p.meta, table="mail_alias_requests",
                    row_id=row["id"], after={"address": address})
    return row


# ── App passwords ───────────────────────────────────────────────────────────

class AppPasswordIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.get("/users/{uid}/app-passwords")
async def list_app_passwords(uid: str, p: Principal = Depends(current_user)):
    service.require_enabled()
    async with db.connection() as conn:
        return await service.app_passwords(conn, _self(p, uid))


@router.post("/users/{uid}/app-passwords")
async def create_app_password(uid: str, body: AppPasswordIn, p: Principal = Depends(current_user)):
    """The secret is in this response only; neither the app nor the audit log keeps it."""
    service.require_enabled()
    _real(p)
    user_id = _self(p, uid)
    async with db.connection() as conn:
        mb = await _own_mailbox(conn, user_id)
        return await service.create_app_password(conn, mb, body.name, actor_id=p.user_id, meta=p.meta)


@router.delete("/users/{uid}/app-passwords/{password_id}")
async def revoke_app_password(uid: str, password_id: int, p: Principal = Depends(current_user)):
    service.require_enabled()
    _real(p)
    user_id = _self(p, uid)
    async with db.connection() as conn:
        await service.revoke_app_passwords(conn, user_id, actor_id=p.user_id, meta=p.meta, ids=[password_id])
    return {"ok": True}


# ── Send identities ─────────────────────────────────────────────────────────

class IdentityPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    signature: str | None = Field(default=None, max_length=4000)


@router.patch("/users/{uid}/identities/{identity_id}")
async def update_identity(uid: str, identity_id: int, body: IdentityPatch, p: Principal = Depends(current_user)):
    service.require_enabled()
    user_id = _self(p, uid)
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """select i.*, m.stalwart_principal as mailbox_principal from mail_identities i
                                          join mailboxes m on m.id = i.mailbox_id
                                          where i.id = %s and i.user_id = %s for update of i""", (identity_id, user_id))
        if row is None:
            raise HTTPException(404, "Identiteten findes ikke")
        after = {"display_name": body.display_name.strip() if body.display_name is not None else row["display_name"],
                 "signature": body.signature if body.signature is not None else row["signature"]}
        if after == {"display_name": row["display_name"], "signature": row["signature"]}:
            return {k: row[k] for k in ("id", "mailbox_id", "from_address", "display_name", "signature", "updated_at")}
        own = await service.personal_mailbox(conn, user_id)
        try:
            # Identities live in the user's own account; shared addresses are sent from there too.
            await service.backend().set_identity(own["stalwart_principal"], row["from_address"], after["display_name"],
                                                 after["signature"])
        except MailBackendError as e:
            raise service.backend_error(e)
        updated = await db.fetch_one(conn, """update mail_identities set display_name = %s, signature = %s,
                                              updated_at = now() where id = %s
                                              returning id, mailbox_id, from_address, display_name, signature, updated_at""",
                                     (after["display_name"], after["signature"], identity_id))
        # The signature itself is not logged, only whether it changed.
        await audit(conn, "mail.identity.update", actor_id=p.user_id, meta=p.meta, table="mail_identities",
                    row_id=identity_id, before={"display_name": row["display_name"]},
                    after={"display_name": after["display_name"], "address": row["from_address"],
                           "signature_changed": after["signature"] != row["signature"]})
    return updated


# ── Activity ────────────────────────────────────────────────────────────────

async def activity(conn, user_id: int, identity: str | None = None, limit: int = 20) -> list[dict]:
    """The latest status per sent message from the user's identities (Lettermint events)."""
    addresses = [r["from_address"] for r in await db.fetch_all(
        conn, "select from_address from mail_identities where user_id = %s", (user_id,))]
    if identity:
        addresses = [a for a in addresses if a == identity.lower()]
    if not addresses:
        return []
    return await db.fetch_all(conn, """
        select * from (
            select distinct on (coalesce(message_id, id::text)) message_id, from_address, recipient_domain, type as status,
                   at, min(at) over (partition by coalesce(message_id, id::text)) as sent_at
            from mail_events where from_address = any(%s) and type <> 'expired'
            order by coalesce(message_id, id::text), at desc) latest
        order by sent_at desc limit %s""", (addresses, limit))


@router.get("/users/{uid}/activity")
async def get_activity(uid: str, identity: str | None = None, p: Principal = Depends(current_user)):
    service.require_enabled()
    user_id = _self(p, uid)
    async with db.connection() as conn:
        return {"items": await activity(conn, user_id, identity),
                "last_expiry_run": await service.last_retention_run(conn)}


# ── Availability (free/busy and working hours) ──────────────────────────────

class WorkingHours(BaseModel):
    days: list[int] = Field(min_length=0, max_length=7)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class AvailabilityIn(BaseModel):
    publish: bool
    working_hours: WorkingHours
    timezone: str = Field(default="Europe/Copenhagen", min_length=3, max_length=60)


async def availability(conn, user_id: int) -> dict:
    row = await db.fetch_one(conn, "select publish, working_hours, timezone, updated_at from mail_availability "
                                   "where user_id = %s", (user_id,))
    return row or {"publish": True, "working_hours": {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "16:00"},
                   "timezone": "Europe/Copenhagen", "updated_at": None}


@router.get("/users/{uid}/availability")
async def get_availability(uid: str, p: Principal = Depends(current_user)):
    service.require_enabled()
    async with db.connection() as conn:
        return await availability(conn, _self(p, uid))


@router.put("/users/{uid}/availability")
async def put_availability(uid: str, body: AvailabilityIn, p: Principal = Depends(current_user)):
    service.require_enabled()
    user_id = _self(p, uid)
    hours = body.working_hours.model_dump()
    if any(d < 1 or d > 7 for d in hours["days"]) or hours["start"] >= hours["end"]:
        raise HTTPException(400, "Arbejdstiden skal have dage fra 1 til 7 og slutte efter den starter")
    hours["days"] = sorted(set(hours["days"]))
    async with db.connection() as conn:
        mb = await _own_mailbox(conn, user_id)
        before = await availability(conn, user_id)
        try:
            await service.backend().set_availability(mb["stalwart_principal"], publish=body.publish, working_hours=hours,
                                                     tz=body.timezone)
        except MailBackendError as e:
            raise service.backend_error(e)
        row = await db.fetch_one(conn, """
            insert into mail_availability (user_id, publish, working_hours, timezone) values (%s, %s, %s, %s)
            on conflict (user_id) do update set publish = excluded.publish, working_hours = excluded.working_hours,
                timezone = excluded.timezone, updated_at = now()
            returning publish, working_hours, timezone, updated_at""", (user_id, body.publish, Jsonb(hours), body.timezone))
        await audit(conn, "mail.availability.update", actor_id=p.user_id, meta=p.meta, table="mail_availability",
                    row_id=user_id, before={k: before[k] for k in ("publish", "working_hours", "timezone")},
                    after={"publish": body.publish, "working_hours": hours, "timezone": body.timezone})
    return row


# ── Calendars and address books ─────────────────────────────────────────────

class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    colour: str | None = Field(default=None, pattern=COLOUR)
    timezone: str | None = Field(default=None, max_length=60)


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    colour: str | None = Field(default=None, pattern=COLOUR)
    timezone: str | None = Field(default=None, max_length=60)
    freebusy: bool | None = None


def _type(request: Request) -> str:
    """Each collection endpoint is registered for both /calendars and /address-books."""
    return CALENDAR if request.scope["route"].path.startswith("/api/mail/users/{uid}/calendars") else ADDRESS_BOOK


@router.get("/users/{uid}/calendars")
@router.get("/users/{uid}/address-books")
async def list_collections(uid: str, request: Request, p: Principal = Depends(current_user)):
    service.require_enabled()
    type = _type(request)
    async with db.connection() as conn:
        return [c for c in await service.collections_for(conn, _self(p, uid)) if c["type"] == type]


@router.post("/users/{uid}/calendars")
@router.post("/users/{uid}/address-books")
async def create_collection(uid: str, request: Request, body: CollectionIn, p: Principal = Depends(current_user)):
    service.require_enabled()
    type = _type(request)
    user_id = _self(p, uid)
    async with db.connection() as conn:
        mb = await _own_mailbox(conn, user_id)
        if mb["status"] != "active":
            raise HTTPException(409, "Postkassen er ikke aktiv")
        n = await db.fetch_one(conn, "select count(*) as n from mail_collections where owner_id = %s", (user_id,))
        if n["n"] >= 50:
            raise HTTPException(409, "Du kan højst have 50 kalendere og adressebøger")
        return await service.create_collection(
            conn, type=type, kind="personal", owner_id=user_id, owner_principal=mb["stalwart_principal"],
            name=body.name.strip(), colour=body.colour if type == CALENDAR else None,
            timezone=(body.timezone or "Europe/Copenhagen") if type == CALENDAR else None, actor_id=p.user_id, meta=p.meta)


@router.patch("/users/{uid}/calendars/{collection_id}")
@router.patch("/users/{uid}/address-books/{collection_id}")
async def update_collection(uid: str, request: Request, collection_id: str, body: CollectionPatch,
                            p: Principal = Depends(current_user)):
    service.require_enabled()
    type = _type(request)
    user_id = _self(p, uid)
    async with db.connection() as conn:
        col, _ = await service.require_collection(conn, user_id, collection_id, need="manager", lock=True)
        if col["type"] != type:
            raise HTTPException(404, "Ikke fundet")
        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k == "colour"}
        if "name" in changes:
            changes["name"] = changes["name"].strip()
        return await service.update_collection(conn, col, changes, actor_id=p.user_id, meta=p.meta)


@router.delete("/users/{uid}/calendars/{collection_id}")
@router.delete("/users/{uid}/address-books/{collection_id}")
async def delete_collection(uid: str, request: Request, collection_id: str, p: Principal = Depends(current_user)):
    service.require_enabled()
    type = _type(request)
    user_id = _self(p, uid)
    async with db.connection() as conn:
        col, role = await service.require_collection(conn, user_id, collection_id, lock=True)
        if col["type"] != type:
            raise HTTPException(404, "Ikke fundet")
        if role != "owner":
            raise HTTPException(403, "Kun ejeren kan slette den")
        await service.delete_collection(conn, col, actor_id=p.user_id, meta=p.meta)
    return {"ok": True}


@router.post("/users/{uid}/address-books/{collection_id}/import")
async def import_vcards(uid: str, collection_id: str, file: UploadFile, p: Principal = Depends(current_user)):
    service.require_enabled()
    user_id = _self(p, uid)
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Filen er større end 10 MB")
    cards = mb_backend.split_cards(data.decode("utf-8", errors="replace"))
    if not cards:
        raise HTTPException(400, "Filen indeholder ingen kontakter (vCard)")
    async with db.connection() as conn:
        col, _ = await service.require_collection(conn, user_id, collection_id, need="editor")
        if col["type"] != ADDRESS_BOOK:
            raise HTTPException(404, "Ikke fundet")
        try:
            n = await service.backend().put_items(col["owner_principal"], ADDRESS_BOOK, col["slug"],
                                                  {f"{uid_}.vcf": card for uid_, card in cards})
        except MailBackendError as e:
            raise service.backend_error(e)
        count = await service.refresh_count(conn, col)
        await audit(conn, "mail.contacts.import", actor_id=p.user_id, meta=p.meta, table="mail_collections",
                    row_id=col["id"], after={"cards": n, "entries": count, "name": col["name"]})
    return {"imported": n, "entries": count}


@router.get("/collections/{collection_id}/export")
async def export_collection(collection_id: str, p: Principal = Depends(current_user)):
    """The user's own copy of a calendar (.ics) or address book (.vcf) they own or may read."""
    service.require_enabled()
    async with db.connection() as conn:
        col, role = await service.require_collection(conn, p.user_id, collection_id)
        data, ctype, filename = await service.export_collection(col)
        await audit(conn, "mail.collection.export", actor_id=p.user_id, meta=p.meta, table="mail_collections",
                    row_id=col["id"], after={"name": col["name"], "bytes": len(data), "role": role})
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


class ShareByEmail(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern="^(viewer|editor|manager)$")


class ShareRole(BaseModel):
    role: str = Field(pattern="^(viewer|editor|manager)$")


@router.post("/collections/{collection_id}/shares")
async def share_by_email(collection_id: str, body: ShareByEmail, p: Principal = Depends(current_user)):
    service.require_enabled()
    _real(p)
    async with db.connection() as conn:
        col, _ = await service.require_collection(conn, p.user_id, collection_id, need="manager", lock=True)
        user = await _user_by_email(conn, body.email)
        await service.set_collection_share(conn, col, user["id"], body.role, actor_id=p.user_id, meta=p.meta)
        return await service.shares(conn, col["id"])


@router.put("/collections/{collection_id}/shares/{user_id}")
async def set_share(collection_id: str, user_id: int, body: ShareRole, p: Principal = Depends(current_user)):
    service.require_enabled()
    _real(p)
    async with db.connection() as conn:
        col, _ = await service.require_collection(conn, p.user_id, collection_id, need="manager", lock=True)
        await service.set_collection_share(conn, col, user_id, body.role, actor_id=p.user_id, meta=p.meta)
        return await service.shares(conn, col["id"])


@router.delete("/collections/{collection_id}/shares/{user_id}")
async def remove_share(collection_id: str, user_id: int, p: Principal = Depends(current_user)):
    """A manager removes someone; anyone may remove themselves."""
    service.require_enabled()
    async with db.connection() as conn:
        need = "viewer" if user_id == p.user_id else "manager"
        col, _ = await service.require_collection(conn, p.user_id, collection_id, need=need, lock=True)
        await service.set_collection_share(conn, col, user_id, None, actor_id=p.user_id, meta=p.meta)
        return await service.shares(conn, col["id"])


# ── Client setup and archive export ─────────────────────────────────────────

@router.get("/users/{uid}/client-profile")
async def client_profile(uid: str, p: Principal = Depends(current_user)):
    """Apple configuration profile: mail, calendar and contacts in one. Contains no password."""
    service.require_enabled()
    user_id = _self(p, uid)
    async with db.connection() as conn:
        mb = await _own_mailbox(conn, user_id)
        app_name = await settings_store.get(conn, "app_name")
    if not clients.host():
        raise HTTPException(503, "MAIL_HOSTNAME er ikke sat")
    data = clients.mobileconfig(mb["address"], p.display_name or "", app_name)
    return Response(data, media_type="application/x-apple-aspen-config",
                    headers={"Content-Disposition": f'attachment; filename="{mb["local_part"]}.mobileconfig"'})


@router.post("/users/{uid}/archive-export")
async def archive_export(uid: str, p: Principal = Depends(current_user)):
    """Only when archive export is on: the user's mail as .eml in a zip under /Mail-arkiv/ in their files."""
    service.require_enabled()
    _real(p)
    user_id = _self(p, uid)
    async with db.connection() as conn:
        if not await settings_store.get(conn, "mail_archive_export"):
            raise HTTPException(403, "Arkiveksport er ikke slået til")
        mb = await _own_mailbox(conn, user_id)
        job = await jobs.enqueue(conn, "mail.archive_export", {"user_id": user_id},
                                 dedupe_key=f"mail.archive_export:{user_id}", max_attempts=3)
        if job is None:
            raise HTTPException(409, "En eksport er allerede i gang")
        await audit(conn, "mail.archive_export.request", actor_id=p.user_id, meta=p.meta, table="mailboxes",
                    row_id=mb["id"], job_id=job)
    return {"queued": True, "folder": f"/users/{user_id}/Mail-arkiv/"}


# ── Shared mailboxes (members and managers) ─────────────────────────────────

class SharedPatch(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class MemberByEmail(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern="^(reader|sender|manager)$")


class MemberRole(BaseModel):
    role: str = Field(pattern="^(reader|sender|manager)$")


async def _shared(conn, p: Principal, mailbox_id: int, *, manager: bool, lock: bool = False) -> tuple[dict, str]:
    mb = await db.fetch_one(conn, service.MAILBOX_SQL + " where m.id = %s and m.kind = 'shared' and m.status <> 'deleted'",
                            (mailbox_id,))
    role = await service.shared_role(conn, p.user_id, mailbox_id) if mb else None
    if role is None:
        raise HTTPException(404, "Postkassen findes ikke")
    if manager and role != "manager":
        raise HTTPException(403, "Kun postkassens ansvarlige kan det")
    if lock:
        mb = await service.mailbox(conn, mailbox_id, lock=True)
    return mb, role


def shared_out(mb: dict, members: list[dict] | None, role: str | None = None) -> dict:
    return {"id": mb["id"], "address": mb["address"], "display_name": mb["display_name"], "status": mb["status"],
            "quota_mb": mb["quota_mb"], "used_bytes": mb["used_bytes"], "role": role,
            "role_label": service.SHARED_LABEL.get(role) if role else None, "members": members}


@router.get("/shared/{mailbox_id}")
async def get_shared(mailbox_id: int, p: Principal = Depends(current_user)):
    service.require_enabled()
    async with db.connection() as conn:
        mb, role = await _shared(conn, p, mailbox_id, manager=False)
        return shared_out(mb, await service.members(conn, mailbox_id) if role == "manager" else None, role)


@router.patch("/shared/{mailbox_id}")
async def rename_shared(mailbox_id: int, body: SharedPatch, p: Principal = Depends(current_user)):
    service.require_enabled()
    async with db.connection() as conn:
        mb, role = await _shared(conn, p, mailbox_id, manager=True, lock=True)
        name = body.display_name.strip()
        if name != mb["display_name"]:
            await conn.execute("update mailboxes set display_name = %s, updated_at = now() where id = %s", (name, mailbox_id))
            await audit(conn, "mail.shared.update", actor_id=p.user_id, meta=p.meta, table="mailboxes", row_id=mailbox_id,
                        before={"display_name": mb["display_name"]}, after={"display_name": name})
            try:
                await service.backend().update_principal(mb["stalwart_principal"], description=name)
            except MailBackendError as e:
                raise service.backend_error(e)
        return shared_out(await service.mailbox(conn, mailbox_id), await service.members(conn, mailbox_id), role)


@router.post("/shared/{mailbox_id}/members")
async def add_member(mailbox_id: int, body: MemberByEmail, p: Principal = Depends(current_user)):
    service.require_enabled()
    _real(p)
    async with db.connection() as conn:
        mb, role = await _shared(conn, p, mailbox_id, manager=True, lock=True)
        user = await _user_by_email(conn, body.email)
        await service.set_member(conn, mb, user["id"], body.role, actor_id=p.user_id, meta=p.meta, admin=False)
        return shared_out(mb, await service.members(conn, mailbox_id), role)


@router.put("/shared/{mailbox_id}/members/{user_id}")
async def set_member(mailbox_id: int, user_id: int, body: MemberRole, p: Principal = Depends(current_user)):
    service.require_enabled()
    _real(p)
    async with db.connection() as conn:
        mb, role = await _shared(conn, p, mailbox_id, manager=True, lock=True)
        await service.set_member(conn, mb, user_id, body.role, actor_id=p.user_id, meta=p.meta, admin=False)
        return shared_out(mb, await service.members(conn, mailbox_id), role)


@router.delete("/shared/{mailbox_id}/members/{user_id}")
async def remove_member(mailbox_id: int, user_id: int, p: Principal = Depends(current_user)):
    """A manager removes a member; any member may leave (user_id = their own id)."""
    service.require_enabled()
    async with db.connection() as conn:
        mb, role = await _shared(conn, p, mailbox_id, manager=user_id != p.user_id, lock=True)
        await service.set_member(conn, mb, user_id, None, actor_id=p.user_id, meta=p.meta, admin=False)
        return {"ok": True}
