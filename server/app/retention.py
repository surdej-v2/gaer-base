"""Deletion of log data. Runs on the separate purge database user, never the app user.

Every purge writes log.purged to the audit log in the same transaction, with counts.
Automatic retention runs once a day as the job retention.logs (app.jobs, job_schedules); /admin/retention can
run it now or purge everything about one user.
"""

import logging
from datetime import date

from app import config, db, settings_store
from app.logs import RequestMeta, audit
from app.security import phone_hash

log = logging.getLogger("gaer.retention")

LOGS = ("auth_log", "audit_log", "analytics", "mcp_log")


async def purge_old(*, actor_id: int | None, meta: RequestMeta, reason: str) -> dict:
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
    days = {
        "auth_log": int(values["retention_auth_log_days"]),
        "audit_log": int(values["retention_audit_log_days"]),
        "analytics": int(values["retention_analytics_days"]),
        "mcp_log": int(values["retention_mcp_log_days"]),
    }
    counts = {}
    async with db.purge_connection() as conn:
        counts["auth_log"] = (await conn.execute(
            "delete from auth_log where ts < now() - make_interval(days => %s)", (days["auth_log"],))).rowcount
        # log.purged and gdpr.erasure entries are kept, so the history of deletions survives.
        counts["audit_log"] = (await conn.execute(
            """delete from audit_log where ts < now() - make_interval(days => %s)
               and action not in ('log.purged', 'gdpr.erasure')""",
            (days["audit_log"],))).rowcount
        counts["analytics"] = (await conn.execute(
            "delete from analytics_events where ts < now() - make_interval(days => %s)",
            (days["analytics"],))).rowcount
        counts["consents"] = (await conn.execute(
            "delete from consents where ts < now() - make_interval(days => %s)", (days["analytics"],))).rowcount
        counts["feature_usage"] = (await conn.execute(
            "delete from feature_usage where ts < now() - make_interval(days => %s)", (days["analytics"],))).rowcount
        counts["mcp_log"] = (await conn.execute(
            "delete from mcp_log where ts < now() - make_interval(days => %s)", (days["mcp_log"],))).rowcount
        await audit(conn, "log.purged", actor_id=actor_id, meta=meta, reason=reason, retention_days=days,
                    deleted=counts)
    async with db.connection() as conn:
        await settings_store.set_many(conn, {"retention_last_run": date.today().isoformat()})
    return counts


async def purge_user(user_id: int, *, logs: list[str], actor_id: int | None, meta: RequestMeta,
                     reason: str, contacts: list[str | None] | None = None) -> dict:
    """Deletes log data about one user. Auth events logged before the user existed (matched on the hash of
    their e-mail address or phone number) and visitor ids the user consented under are included."""
    counts = {}
    async with db.purge_connection() as conn:
        if contacts is None:
            row = await db.fetch_one(conn, "select email, phone from users where id = %s", (user_id,))
            contacts = [row["email"], row["phone"]] if row else []
        hashes = [phone_hash(c) for c in contacts if c]
        if "auth_log" in logs:
            counts["auth_log"] = (await conn.execute(
                "delete from auth_log where user_id = %s or phone_hash = any(%s)", (user_id, hashes))).rowcount
        if "audit_log" in logs:
            counts["audit_log"] = (await conn.execute("""
                delete from audit_log
                where action not in ('log.purged', 'gdpr.erasure')   -- the record that we erased must survive
                  and (actor_user_id = %s or (table_name = 'users' and row_id = %s))""",
                (user_id, str(user_id)))).rowcount
        if "analytics" in logs:
            counts["analytics"] = (await conn.execute("""
                delete from analytics_events
                where user_id = %s
                   or visitor_id in (select visitor_id from consents where user_id = %s and visitor_id is not null)""",
                (user_id, user_id))).rowcount
            counts["consents"] = (await conn.execute(
                "delete from consents where user_id = %s", (user_id,))).rowcount
            counts["feature_usage"] = (await conn.execute(
                "delete from feature_usage where user_id = %s", (user_id,))).rowcount
        if "mcp_log" in logs:
            counts["mcp_log"] = (await conn.execute("delete from mcp_log where user_id = %s", (user_id,))).rowcount
        await audit(conn, "log.purged", actor_id=actor_id, meta=meta, reason=reason, subject_user_id=user_id,
                    deleted=counts)
    return counts


async def log_stats() -> list[dict]:
    async with db.connection() as conn:
        rows = []
        for name, table in (("auth_log", "auth_log"), ("audit_log", "audit_log"), ("analytics", "analytics_events"),
                            ("mcp_log", "mcp_log")):
            r = await db.fetch_one(conn, f"select count(*) as rows, min(ts) as oldest from {table}")
            size = await db.fetch_one(conn, "select pg_total_relation_size(%s) as bytes", (table,))
            rows.append({"log": name, "rows": r["rows"], "oldest": r["oldest"], "bytes": size["bytes"]})
        return rows


# ── Operational data (retention_policies) ───────────────────────────────────
# Each policy key has fixed SQL here; the table only holds the period and whether it is on. Runs on the app user
# (these are not log tables) from the hourly job retention.cleanup, and writes retention.cleanup to the audit log.

CLEANUP_SQL = {
    "jobs": "delete from jobs where status in ('succeeded', 'failed', 'dead') and finished_at < now() - make_interval(days => %s)",
    "webhook_deliveries": """delete from webhook_deliveries where status in ('succeeded', 'dead')
                             and created_at < now() - make_interval(days => %s)""",
    "otp_codes": "delete from otp_codes where created_at < now() - make_interval(days => %s)",
    "webauthn_challenges": "delete from webauthn_challenges where expires_at < now() - make_interval(days => %s)",
    "sessions": "delete from sessions where expires_at < now() - make_interval(days => %s)",
    "webhook_inbound": """delete from webhook_inbound where status in ('processed', 'ignored', 'failed')
                          and received_at < now() - make_interval(days => %s)""",
    "captcha_spent": "delete from captcha_spent where expires_at < now() - make_interval(days => %s)",
    "mcp_access_requests": """delete from mcp_access_requests where updated_at < now() - make_interval(days => %s)
                              and (status in ('denied', 'withdrawn', 'revoked')
                                   or (status = 'approved' and expires_at < now())
                                   or (status in ('awaiting_user', 'awaiting_approver') and session_id is null))""",
    "knowledge_ingest_log": "delete from knowledge_ingest_log where created_at < now() - make_interval(days => %s)",
    "knowledge_conversations": "delete from knowledge_conversations where updated_at < now() - make_interval(days => %s)",
    "review_orphans": """delete from review_sessions s where ended_at < now() - make_interval(days => %s)
                         and jsonb_array_length(pages) = 0
                         and not exists (select 1 from review_feedback r where r.session_id = s.id)""",
}


async def cleanup(*, only: str | None = None, actor_id: int | None = None, meta: RequestMeta | None = None) -> dict:
    """Deletes operational data older than each enabled policy. Returns {policy: rows deleted}."""
    counts = {}
    async with db.connection() as conn:
        policies = await db.fetch_all(conn, "select key, days from retention_policies where enabled"
                                      + (" and key = %s" if only else "") + " order by key", (only,) if only else ())
        for p in policies:
            sql = CLEANUP_SQL.get(p["key"])
            if sql is None:
                continue
            n = (await conn.execute(sql, (p["days"],))).rowcount
            counts[p["key"]] = n
            await conn.execute("update retention_policies set last_run_at = now(), last_deleted = %s where key = %s",
                               (n, p["key"]))
        if any(counts.values()) or actor_id is not None:
            await audit(conn, "retention.cleanup", actor_id=actor_id, meta=meta or RequestMeta(), deleted=counts)
    return counts


async def policies() -> list[dict]:
    async with db.connection() as conn:
        return await db.fetch_all(conn, "select * from retention_policies order by key")


def enabled() -> bool:
    return config.settings.run_maintenance
