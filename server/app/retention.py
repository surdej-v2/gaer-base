"""Deletion of log data. Runs on the separate purge database user, never the app user.

Every purge writes log.purged to the audit log in the same transaction, with counts.
Automatic retention runs once a day from the maintenance loop; /admin/retention can
run it now or purge everything about one user.
"""

import asyncio
import logging
from datetime import date, timedelta

from app import config, db, settings_store
from app.logs import RequestMeta, audit
from app.security import phone_hash

log = logging.getLogger("gaer.retention")

LOGS = ("auth_log", "audit_log", "analytics")


async def purge_old(*, actor_id: int | None, meta: RequestMeta, reason: str) -> dict:
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
    days = {
        "auth_log": int(values["retention_auth_log_days"]),
        "audit_log": int(values["retention_audit_log_days"]),
        "analytics": int(values["retention_analytics_days"]),
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
        await audit(conn, "log.purged", actor_id=actor_id, meta=meta, reason=reason, retention_days=days,
                    deleted=counts)
    async with db.connection() as conn:
        await settings_store.set_many(conn, {"retention_last_run": date.today().isoformat()})
    return counts


async def purge_user(user_id: int, *, logs: list[str], actor_id: int | None, meta: RequestMeta,
                     reason: str, phone: str | None = None) -> dict:
    """Deletes log data about one user. Auth events logged before the user existed (matched on
    the phone hash) and visitor ids the user consented under are included."""
    counts = {}
    async with db.purge_connection() as conn:
        if phone is None:
            row = await db.fetch_one(conn, "select phone from users where id = %s", (user_id,))
            phone = row["phone"] if row else None
        if "auth_log" in logs:
            counts["auth_log"] = (await conn.execute(
                "delete from auth_log where user_id = %s or (%s::text is not null and phone_hash = %s)",
                (user_id, phone_hash(phone), phone_hash(phone)))).rowcount
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
        await audit(conn, "log.purged", actor_id=actor_id, meta=meta, reason=reason, subject_user_id=user_id,
                    deleted=counts)
    return counts


async def log_stats() -> list[dict]:
    async with db.connection() as conn:
        rows = []
        for name, table in (("auth_log", "auth_log"), ("audit_log", "audit_log"), ("analytics", "analytics_events")):
            r = await db.fetch_one(conn, f"select count(*) as rows, min(ts) as oldest from {table}")
            size = await db.fetch_one(conn, "select pg_total_relation_size(%s) as bytes", (table,))
            rows.append({"log": name, "rows": r["rows"], "oldest": r["oldest"], "bytes": size["bytes"]})
        return rows


# ── Maintenance loop ────────────────────────────────────────────────────────

async def maintenance_once() -> None:
    async with db.connection() as conn:
        await conn.execute("delete from otp_codes where created_at < now() - interval '1 day'")
        await conn.execute("delete from webauthn_challenges where expires_at < now()")
        await conn.execute("delete from sessions where expires_at < now() - interval '30 days'")
        today = date.today()
        await conn.execute("select gaer_ensure_analytics_partition(%s)", (today,))
        await conn.execute("select gaer_ensure_analytics_partition(%s)", (today.replace(day=1) + timedelta(days=32),))
        last_run = await settings_store.get(conn, "retention_last_run")
    if last_run != date.today().isoformat():
        counts = await purge_old(actor_id=None, meta=RequestMeta(), reason="retention")
        log.info("retention purge: %s", counts)


async def maintenance_loop() -> None:
    while True:
        try:
            await maintenance_once()
        except Exception:
            log.exception("maintenance failed")
        await asyncio.sleep(3600)


def enabled() -> bool:
    return config.settings.run_maintenance
