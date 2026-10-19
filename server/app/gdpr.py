"""GDPR: one export and one erasure, used both from /admin/gdpr and by users themselves (/api/me).

Rule: every table with personal data must appear in build_user_export and be removed by erase_user.
"""

import io
import json
import re
import zipfile
from datetime import datetime

from fastapi import HTTPException

from app import db, retention
from app.logs import RequestMeta, audit
from app.storage import get_storage


async def build_user_export(conn, user_id: int) -> dict:
    """Everything stored about one user, as data. Files are added to the zip by export_zip."""
    async def rows(sql, *params):
        return await db.fetch_all(conn, sql, params)

    user = await db.fetch_one(conn, """select id, phone, display_name, role, grace_logins_used, blocked_at,
                                              created_at, updated_at, last_login_at from users where id = %s""", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    visitor_ids = [r["visitor_id"] for r in await rows(
        "select distinct visitor_id from consents where user_id = %s and visitor_id is not null", user_id)]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "user": user,
        "passkeys": await rows("select id, name, created_at, last_used_at from passkeys where user_id = %s", user_id),
        "sessions": await rows("""select created_at, last_seen_at, expires_at, revoked_at, host(ip) as ip, user_agent
                                  from sessions where user_id = %s order by created_at""", user_id),
        "files": await rows("""select id, filename, content_type, size_bytes, created_at from files
                               where user_id = %s order by created_at""", user_id),
        "clips": await rows("""select id, source, title, body, url, created_at, updated_at from clips
                               where user_id = %s order by created_at""", user_id),
        "bridge_consents": await rows("select domain, created_at from bridge_consents where user_id = %s order by domain",
                                      user_id),
        "gdpr_requests": await rows("""select id, kind, status, message, received_at, completed_at from gdpr_requests
                                       where user_id = %s""", user_id),
        "consents": await rows("select ts, visitor_id, statistics, version, device from consents where user_id = %s",
                               user_id),
        "auth_log": await rows("""select ts, event, result, host(ip) as ip, device, detail from auth_log
                                  where user_id = %s order by ts""", user_id),
        "audit_log": await rows("""select ts, action, table_name, row_id, before, after, host(ip) as ip from audit_log
                                   where (actor_user_id = %s or (table_name = 'users' and row_id = %s))
                                     and action <> 'admin.read' order by ts""", user_id, str(user_id)),
        "feature_level": await rows("select ring, updated_at from feature_rings where user_id = %s", user_id),
        "feature_usage": await rows("select ts, feature_key from feature_usage where user_id = %s order by ts limit 50000",
                                    user_id),
        "test_accounts": await rows("""select id, display_name, role, created_at from users
                                       where impersonation_only and impersonation_owner_id = %s""", user_id),
        "ai_usage": await rows("""select ts, surface, model, prompt_tokens, completion_tokens from ai_usage
                                  where user_id = %s order by ts""", user_id),
        "analytics_events": await rows("""select ts, kind, path, referrer, device, browser from analytics_events
                                          where user_id = %s or visitor_id = any(%s) order by ts limit 50000""",
                                       user_id, visitor_ids),
    }


README = """Indsigt i dine data

data.json   alt, vi har gemt om dig: bruger, passkeys, sessioner, klip, filer (oversigt), tilladte domæner,
            GDPR-anmodninger, samtykker, login-log, ændringslog, AI-forbrug, feature-niveau og -brug,
            testkonti du har oprettet og statistik knyttet til dig.
files/      kopier af de filer, du har uploadet.

Samtaler med AI'en bliver ikke gemt og er derfor ikke med.
"""


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.\- ]+", "_", name)[:120] or "fil"


async def export_zip(conn, user_id: int) -> bytes:
    data = await build_user_export(conn, user_id)
    keys = {r["id"]: r["storage_key"] for r in await db.fetch_all(
        conn, "select id, storage_key from files where user_id = %s", (user_id,))}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LÆS MIG.txt", README)
        z.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=2, default=str))
        for f in data["files"]:
            try:
                content, _ = await get_storage().get(keys[f["id"]])
            except Exception:  # a missing object must not block the rest of the export
                z.writestr(f"files/{f['id']}-MANGLER.txt", "Filen kunne ikke hentes fra lageret.")
                continue
            z.writestr(f"files/{f['id']}-{_safe_name(f['filename'])}", content)
    return buf.getvalue()


def export_filename(user_id: int) -> str:
    return f"indsigt-{user_id}-{datetime.now():%Y%m%d}.zip"


async def ensure_can_erase(conn, user_id: int) -> dict:
    user = await db.fetch_one(conn, "select * from users where id = %s for update", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    if user["role"] == "admin":
        n = await db.fetch_one(conn, "select count(*) as n from users where role = 'admin' and blocked_at is null"
                                   " and not impersonation_only")
        if n["n"] <= 1:
            raise HTTPException(409, "Den sidste administrator kan ikke slettes. Gør en anden til admin først.")
    return user


async def erase_user(user_id: int, *, actor_id: int | None, meta: RequestMeta, reason: str,
                     gdpr_request: int | None = None) -> dict:
    """The right to be forgotten. Deletes the user (passkeys, sessions, clips, domain consents, AI usage, one-time
    codes, feature level and their test accounts cascade; feature usage is purged with the logs), their files in Object Storage, the contact details on their GDPR requests, and
    log data about them (via the purge user). The erasure itself is logged without personal data."""
    async with db.connection() as conn:
        user = await ensure_can_erase(conn, user_id)
        files = await db.fetch_all(conn, "delete from files where user_id = %s returning storage_key", (user_id,))
        await conn.execute("""update gdpr_requests set contact = 'slettet', message = '',
                              status = case when status in ('received', 'in_progress') and kind = 'erasure'
                                            then 'completed' else status end,
                              completed_at = coalesce(completed_at, now()), handled_by = coalesce(handled_by, %s)
                              where user_id = %s or id = %s""", (actor_id, user_id, gdpr_request))
        await conn.execute("delete from users where id = %s", (user_id,))
        await audit(conn, "gdpr.erasure", actor_id=actor_id if actor_id != user_id else None, meta=meta,
                    table="users", row_id=user_id, reason=reason, gdpr_request=gdpr_request, files=len(files),
                    by_self=actor_id == user_id)
    for f in files:
        try:
            await get_storage().delete(f["storage_key"])
        except Exception:
            pass  # the row is gone; an orphaned object is found by the storage overview
    counts = await retention.purge_user(user_id, logs=list(retention.LOGS),
                                        actor_id=actor_id if actor_id != user_id else None, meta=meta,
                                        reason=reason, phone=user["phone"])
    return {"files": len(files), "purged": counts}
