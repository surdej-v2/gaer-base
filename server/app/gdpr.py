"""GDPR: one export and one erasure, used both from /admin/gdpr and by users themselves (/api/me).

Rule: every table with personal data must appear in build_user_export and be removed by erase_user.
"""

import io
import json
import re
import zipfile
from datetime import datetime

from fastapi import HTTPException

from app import db, jobs, retention, webhooks
from app.logs import RequestMeta, audit
from app.storage import get_storage


async def build_user_export(conn, user_id: int) -> dict:
    """Everything stored about one user, as data. Files are added to the zip by export_zip."""
    async def rows(sql, *params):
        return await db.fetch_all(conn, sql, params)

    user = await db.fetch_one(conn, """select id, email, phone, display_name, locale, time_zone, role, super_admin, admin_roles,
                                              mfa_required, grace_logins_used, blocked_at, audience, created_at, updated_at,
                                              last_login_at, avatar_file_id
                                       from users where id = %s""", (user_id,))
    if user is None:
        raise HTTPException(404, "Brugeren findes ikke")
    visitor_ids = [r["visitor_id"] for r in await rows(
        "select distinct visitor_id from consents where user_id = %s and visitor_id is not null", user_id)]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "user": user,
        "passkeys": await rows("select id, name, kind, created_at, last_used_at from passkeys where user_id = %s", user_id),
        # Authenticator apps: only that they exist, never the secret.
        "totp_factors": await rows("""select id, name, created_at, confirmed_at, last_used_at from totp_factors
                                      where user_id = %s order by created_at""", user_id),
        "sessions": await rows("""select created_at, last_seen_at, expires_at, revoked_at, host(ip) as ip, user_agent,
                                         mfa_at
                                  from sessions where user_id = %s order by created_at""", user_id),
        "files": await rows("""select id, filename, folder, content_type, size_bytes, kind, created_at, updated_at
                               from files where user_id = %s order by created_at""", user_id),
        # Files you put in /shared are common project files: they stay when you are erased (without your name),
        # so only which ones they are is exported, not their content.
        "shared_files": await rows("""select id, '/shared' || folder || filename as path, content_type, size_bytes,
                                             created_at from files
                                      where space = 'shared' and uploaded_by = %s order by created_at""", user_id),
        "clips": await rows("""select id, source, title, body, url, created_at, updated_at from clips
                               where user_id = %s order by created_at""", user_id),
        # Mit område: who you have given access to your area, and whose areas you have access to.
        "delegations_given": await rows("""select delegate_email, role, created_at, updated_at, expires_at, accepted_at,
                                                  last_used_at from user_delegations
                                           where owner_id = %s order by created_at""", user_id),
        "delegations_received": await rows("""select owner_id, role, created_at, updated_at, expires_at, accepted_at,
                                                     last_used_at from user_delegations
                                              where delegate_id = %s
                                                 or (delegate_id is null and delegate_email = %s)
                                              order by created_at""", user_id, user["email"]),
        "knowledge_agent_members": await rows("""select a.name as agent, a.slug, m.privilege, m.created_at
                                                from knowledge_agent_members m join knowledge_agents a on a.id = m.agent_id
                                                where m.user_id = %s order by a.name""", user_id),
        "knowledge_conversations": await rows("""
            select c.id, a.name as agent, c.title, c.created_at, c.updated_at,
                   coalesce((select jsonb_agg(jsonb_build_object('role', m.role, 'text', m.text, 'citations', m.citations,
                                                                 'agent_version', m.agent_version, 'created_at', m.created_at)
                                              order by m.id)
                             from knowledge_messages m where m.conversation_id = c.id), '[]'::jsonb) as messages
            from knowledge_conversations c join knowledge_agents a on a.id = c.agent_id
            where c.user_id = %s order by c.created_at""", user_id),
        # Mail (specs/mail): the app's records of your mailbox. The mail, events and contacts themselves are in the
        # mail server; you fetch them with your mail client, or as a zip from /user/mail (archive export).
        "mailbox": await rows("""select m.local_part || '@' || d.name as address, m.kind, m.status, m.quota_mb,
                                        m.used_bytes, m.requested_at, m.provisioned_at, m.last_login_at
                                 from mailboxes m join mail_domains d on d.id = m.domain_id
                                 where m.owner_id = %s order by m.created_at""", user_id),
        "mail_aliases": await rows("""select a.address, a.created_at from mail_aliases a
                                      join mailboxes m on m.id = a.mailbox_id where m.owner_id = %s order by a.address""",
                                   user_id),
        "mail_alias_requests": await rows("""select address, note, status, created_at, decided_at from mail_alias_requests
                                             where user_id = %s order by created_at""", user_id),
        "mail_shared_memberships": await rows("""select m.local_part || '@' || d.name as address, s.role, s.added_at
                                                 from mail_shared_members s join mailboxes m on m.id = s.mailbox_id
                                                 join mail_domains d on d.id = m.domain_id
                                                 where s.user_id = %s order by address""", user_id),
        "mail_identities": await rows("""select from_address, display_name, signature, updated_at from mail_identities
                                         where user_id = %s order by from_address""", user_id),
        "mail_collections": await rows("""select type, name, colour, timezone, is_default, entry_count, created_at
                                          from mail_collections where owner_id = %s order by type, name""", user_id),
        "mail_collection_shares": await rows("""select c.type, c.name, s.role, s.added_at from mail_collection_shares s
                                                join mail_collections c on c.id = s.collection_id
                                                where s.user_id = %s order by c.name""", user_id),
        "mail_availability": await rows("""select publish, working_hours, timezone, updated_at from mail_availability
                                           where user_id = %s""", user_id),
        "mail_app_passwords": await rows("""select name, created_at, last_used_at, revoked_at from mail_app_passwords
                                            where user_id = %s order by created_at""", user_id),
        "mail_events": await rows("""select e.type, e.message_id, e.from_address, e.recipient_domain, e.count, e.at
                                     from mail_events e join mailboxes m on m.id = e.mailbox_id
                                     where m.owner_id = %s order by e.at limit 50000""", user_id),
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
        "review_sessions": await rows("""select id, persona_key, persona, script_key, script_title, ring, tasks, pages,
                                                notes, started_at, ended_at from review_sessions
                                         where user_id = %s order by started_at""", user_id),
        "review_feedback": await rows("""select r.id, r.session_id, r.task_id, r.path, r.body, r.impression, r.feature_key,
                                                r.ring, r.target, r.created_at, r.updated_at,
                                                coalesce(array_agg(a.file_id) filter (where a.file_id is not null), '{}')
                                                  as file_ids
                                         from review_feedback r left join review_attachments a on a.feedback_id = r.id
                                         where r.user_id = %s group by r.id order by r.created_at""", user_id),
        "test_accounts": await rows("""select id, display_name, role, created_at from users
                                       where impersonation_only and impersonation_owner_id = %s""", user_id),
        "mcp_access_requests": await rows("""select r.id, r.endpoint, r.client_name, r.target, r.tool_name, r.level,
                                                    r.reason, r.status, r.user_decided_at, r.user_via,
                                                    r.approver_decided_at, r.approver_via, r.note, r.expires_at,
                                                    r.created_at from mcp_access_requests r
                                             where r.user_id = %s order by r.created_at""", user_id),
        "ai_usage": await rows("""select ts, surface, model, prompt_tokens, completion_tokens from ai_usage
                                  where user_id = %s order by ts""", user_id),
        "ai_budgets": await rows("""select monthly_tokens, warn_pcts, hard, total_tokens, starts_on, ends_on, note,
                                           created_at, updated_at from ai_budgets where user_id = %s order by starts_on""",
                                 user_id),
        "ai_budget_alerts": await rows("""select month, kind, pct, used, limit_tokens, created_at, mailed_at from ai_budget_alerts
                                          where user_id = %s order by created_at""", user_id),
        "analytics_events": await rows("""select ts, kind, path, referrer, device, browser from analytics_events
                                          where user_id = %s or visitor_id = any(%s) order by ts limit 50000""",
                                       user_id, visitor_ids),
    }


README = """Indsigt i dine data

data.json   alt, vi har gemt om dig: bruger og profil, passkeys og sikkerhedsnøgler, godkendelsesapps
            (uden den hemmelige nøgle), sessioner, klip, filer (oversigt), filer du har lagt i /shared
            (kun oversigt), hvem du har delt dit område med og hvis områder du har adgang til, tilladte domæner,
            din postkasse (adresse, aliasser, fælles postkasser, afsenderidentiteter, kalendere og adressebøger
            og hvem de er delt med, app-adgangskoder uden selve koden, leveringshændelser),
            GDPR-anmodninger, samtykker, login-log, ændringslog, AI-forbrug, feature-niveau og -brug,
            MCP-adgangsanmodninger (hvad dine MCP-klienter har bedt om, og hvem der godkendte),
            review-sessioner og -noter, testkonti du har oprettet og statistik knyttet til dig.
files/      kopier af de filer, du har uploadet, dit profilbillede og skærmbilleder og lyd fra review-noter.

Samtaler med AI'en bliver ikke gemt og er derfor ikke med. Dine mails, kalenderaftaler og kontakter ligger i
mailserveren: hent dem med din mailklient, eksportér kalendere og adressebøger på /user/mail, eller bed om et
mailarkiv der, hvis arkiveksport er slået til.
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
    """The right to be forgotten. Deletes the user (passkeys, authenticator apps, sessions, clips, domain consents, AI usage,
    AI budgets and their alerts, one-time codes, feature level, review sessions and notes, their test accounts, access to
    and from their area (/user), Viden conversations and agent memberships cascade; their mailbox rows cascade and the
    mailbox itself is removed in Stalwart by the job mail.principal.delete; review screenshots and audio are files;
    feature usage is purged with the logs; their files in Object Storage (their whole home and folders cascade; files they
    put in /shared stay, with the uploader link set to null); the contact details on their GDPR requests, and
    log data about them (via the purge user). The erasure itself is logged without personal data."""
    async with db.connection() as conn:
        user = await ensure_can_erase(conn, user_id)
        if user["super_admin"] and actor_id != user_id:
            raise HTTPException(409, "Superadministratoren kan kun slette sin egen konto")
        files = await db.fetch_all(conn, "delete from files where user_id = %s returning storage_key", (user_id,))
        await conn.execute("""update gdpr_requests set contact = 'slettet', message = '',
                              status = case when status in ('received', 'in_progress') and kind = 'erasure'
                                            then 'completed' else status end,
                              completed_at = coalesce(completed_at, now()), handled_by = coalesce(handled_by, %s)
                              where user_id = %s or id = %s""", (actor_id, user_id, gdpr_request))
        # Mail: the personal mailbox is removed in Stalwart by a job (with retries); the app's rows (mailbox, aliases,
        # identities, collections, shares, app passwords, events) cascade with the user.
        for mb in await db.fetch_all(conn, """select stalwart_principal from mailboxes where owner_id = %s
                                              and status in ('provisioning', 'active', 'suspended')""", (user_id,)):
            await jobs.enqueue(conn, "mail.principal.delete", {"principal": mb["stalwart_principal"]},
                               dedupe_key=f"mail.principal.delete:{mb['stalwart_principal']}", max_attempts=10)
        # Delegations cascade with the user; access given to their e-mail before they claimed it is removed here.
        if user["email"]:
            await conn.execute("delete from user_delegations where delegate_id is null and delegate_email = %s",
                               (user["email"],))
        await conn.execute("delete from users where id = %s", (user_id,))
        await audit(conn, "gdpr.erasure", actor_id=actor_id if actor_id != user_id else None, meta=meta,
                    table="users", row_id=user_id, reason=reason, gdpr_request=gdpr_request, files=len(files),
                    by_self=actor_id == user_id)
        await webhooks.emit(conn, "user.deleted", {"user_id": user_id})
    for f in files:
        try:
            await get_storage().delete(f["storage_key"])
        except Exception:
            pass  # the row is gone; an orphaned object is found by the storage overview
    counts = await retention.purge_user(user_id, logs=list(retention.LOGS),
                                        actor_id=actor_id if actor_id != user_id else None, meta=meta,
                                        reason=reason, contacts=[user["email"], user["phone"]])
    return {"files": len(files), "purged": counts}
