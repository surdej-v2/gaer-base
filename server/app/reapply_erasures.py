"""Re-applies GDPR erasures after a database restore.

A restored backup can bring back people who were erased after the backup was taken. Their erasure is
recorded (without personal data) as gdpr.erasure in the audit log of the database you are replacing.
Run this against the restored database before it goes live:

    # ids from the old database's audit log since the backup time
    python -m app.reapply_erasures --from-url "$OLD_DATABASE_URL" --since 2026-10-01T03:00:00Z

    # or an explicit list of user ids
    python -m app.reapply_erasures --ids 12,15

The target is the instance's own configuration (DATABASE_URL, PURGE_DATABASE_URL, storage). Users that no
longer exist are skipped, so running it twice is safe. Add --dry-run to only list what would be erased.
"""

import argparse
import asyncio
import sys

import psycopg

from app import db, gdpr
from app.logs import RequestMeta


def erased_ids_since(source_url: str, since: str) -> list[int]:
    with psycopg.connect(source_url) as conn:
        rows = conn.execute("""select distinct row_id from audit_log
                               where action = 'gdpr.erasure' and table_name = 'users' and ts >= %s""",
                            (since,)).fetchall()
    return sorted(int(r[0]) for r in rows if r[0] and r[0].isdigit())


async def reapply(ids: list[int], dry_run: bool) -> int:
    await db.open_pools()
    try:
        return await reapply_ids(ids, dry_run)
    finally:
        await db.close_pools()


async def reapply_ids(ids: list[int], dry_run: bool = False) -> int:
    """Erases each user that exists again. Returns the number of failures."""
    failures = 0
    for user_id in ids:
        async with db.connection() as conn:
            exists = await db.fetch_one(conn, "select 1 from users where id = %s", (user_id,))
        if not exists:
            print(f"#{user_id}: findes ikke (allerede slettet)")
            continue
        if dry_run:
            print(f"#{user_id}: ville blive slettet")
            continue
        try:
            result = await gdpr.erase_user(user_id, actor_id=None, meta=RequestMeta(),
                                           reason="reapply_after_restore")
            print(f"#{user_id}: slettet igen ({result['files']} filer)")
        except Exception as e:  # e.g. the last admin — report and continue with the rest
            failures += 1
            print(f"#{user_id}: FEJL — {getattr(e, 'detail', e)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-url", help="database to read gdpr.erasure entries from (the one being replaced)")
    parser.add_argument("--since", help="backup time (ISO 8601); erasures from then on are re-applied")
    parser.add_argument("--ids", help="comma-separated user ids instead of --from-url/--since")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.ids:
        ids = sorted({int(x) for x in args.ids.split(",") if x.strip()})
    elif args.from_url and args.since:
        ids = erased_ids_since(args.from_url, args.since)
    else:
        parser.error("brug --ids eller --from-url sammen med --since")
    print(f"{len(ids)} sletning(er) at genanvende")
    return 1 if asyncio.run(reapply(ids, args.dry_run)) else 0


if __name__ == "__main__":
    sys.exit(main())
