"""Monitoring of ClickHouse: the checks behind the job clickhouse.monitor and the read-only views of /admin/clickhouse.

Every query here is fixed in the code (VIEWS and the *_SQL constants); /admin can pick a view but never send SQL.
The job runs every 5 minutes (job_schedules "clickhouse.monitor"), stores each run in clickhouse_checks and, when the
overall status changes (ok → warn → error and back), writes clickhouse.status to the audit log and emits the webhook
event clickhouse.status.changed. Thresholds are below; ClickHouse itself delays inserts at 1000 parts in a partition.
"""

import asyncio
import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from app import clickhouse, clickhouse_migrate, db, jobs, webhooks
from app.logs import RequestMeta, audit

log = logging.getLogger("gaer.clickhouse")

DISK_FREE_WARN = 0.20      # share of the disk that is free
DISK_FREE_ERROR = 0.10
PARTS_WARN = 300           # active parts in the busiest partition
PARTS_ERROR = 1000
MUTATION_STUCK_MINUTES = 60

_RANK = {"ok": 0, "warn": 1, "error": 2}

SERVER_SQL = """SELECT version() AS version, uptime() AS uptime_seconds, timezone() AS timezone,
                       currentDatabase() AS database"""
DISKS_SQL = "SELECT name, path, free_space, total_space FROM system.disks ORDER BY name"
METRICS_SQL = """SELECT metric, value FROM system.metrics
                 WHERE metric IN ('Query', 'TCPConnection', 'HTTPConnection', 'MemoryTracking',
                                  'BackgroundMergesAndMutationsPoolTask')"""
ASYNC_METRICS_SQL = """SELECT metric, value FROM system.asynchronous_metrics
                       WHERE metric IN ('MaxPartCountForPartition', 'OSMemoryTotal', 'OSMemoryAvailable',
                                        'LoadAverage1', 'NumberOfTables', 'NumberOfDatabases')"""
MUTATIONS_CHECK_SQL = """SELECT countIf(latest_fail_reason != '') AS failing,
                                countIf(create_time < now() - toIntervalMinute({minutes:UInt32})) AS stuck
                         FROM system.mutations WHERE NOT is_done"""
TABLES_SQL = """SELECT database, table, count() AS parts, sum(rows) AS rows, sum(bytes_on_disk) AS bytes,
                       max(modification_time) AS modified
                FROM system.parts WHERE active
                GROUP BY database, table ORDER BY bytes DESC LIMIT {limit:UInt32}"""

# view → (title, SQL). Read only, fixed; parameters are fixed too.
VIEWS: dict[str, tuple[str, str]] = {
    "tables": ("Tabeller", TABLES_SQL.replace("{limit:UInt32}", "200")),
    "queries": ("Kørende forespørgsler", """
        SELECT query_id, user, round(elapsed, 1) AS elapsed_seconds, read_rows, memory_usage,
               substring(query, 1, 300) AS query
        FROM system.processes WHERE query_id != queryID() ORDER BY elapsed DESC LIMIT 50"""),
    "mutations": ("Mutationer", """
        SELECT database, table, mutation_id, substring(command, 1, 300) AS command, create_time, parts_to_do,
               is_done, latest_fail_reason, latest_fail_time
        FROM system.mutations
        WHERE NOT is_done OR latest_fail_reason != '' OR create_time > now() - toIntervalDay(7)
        ORDER BY create_time DESC LIMIT 100"""),
    "errors": ("Fejl de seneste 7 dage", """
        SELECT name, code, value AS count, last_error_time, substring(last_error_message, 1, 300) AS message
        FROM system.errors WHERE last_error_time > now() - toIntervalDay(7)
        ORDER BY last_error_time DESC LIMIT 100"""),
    "slow": ("Langsomme og fejlede forespørgsler (24 timer)", """
        SELECT event_time, type, query_duration_ms, read_rows, memory_usage, exception_code,
               substring(query, 1, 300) AS query
        FROM system.query_log
        WHERE event_date >= yesterday() AND event_time > now() - toIntervalDay(1) AND type != 'QueryStart'
          AND (query_duration_ms >= 1000 OR exception_code != 0)
        ORDER BY event_time DESC LIMIT 100"""),
    "disks": ("Diske", DISKS_SQL),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def duration(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d} d {h} t"
    if h:
        return f"{h} t {m} min"
    return f"{m} min"


def _pct(share: float) -> str:
    return f"{share * 100:.0f} %".replace(".", ",")


async def check() -> dict:
    """Runs every check. Returns {"status", "version", "checks": [{key, label, status, detail}]}."""
    checks: list[dict] = []

    def add(key: str, label: str, status: str, detail: str) -> None:
        checks.append({"key": key, "label": label, "status": status, "detail": detail})

    try:
        server = (await clickhouse.query(SERVER_SQL))[0]
    except (clickhouse.ClickHouseError, IndexError) as e:
        add("reachable", "Svarer", "error", getattr(e, "message", "Intet svar"))
        return {"status": "error", "version": None, "checks": checks}
    add("reachable", "Svarer", "ok", f"Version {server['version']}, oppe i {duration(server['uptime_seconds'])}")

    async def guarded(key: str, label: str, fn) -> None:
        try:
            await fn()
        except clickhouse.ClickHouseError as e:
            add(key, label, "warn", f"Kunne ikke tjekkes: {e.message}")

    async def disks() -> None:
        rows = await clickhouse.query(DISKS_SQL)
        worst = min(rows, key=lambda d: d["free_space"] / d["total_space"] if d["total_space"] else 1, default=None)
        if worst is None or not worst["total_space"]:
            add("disk", "Diskplads", "ok", "Ingen diske med kendt størrelse")
            return
        share = worst["free_space"] / worst["total_space"]
        status = "error" if share < DISK_FREE_ERROR else "warn" if share < DISK_FREE_WARN else "ok"
        add("disk", "Diskplads", status, f"{_pct(share)} fri på {worst['name']}")

    async def parts() -> None:
        metrics = {r["metric"]: r["value"] for r in await clickhouse.query(ASYNC_METRICS_SQL)}
        n = int(metrics.get("MaxPartCountForPartition", 0))
        status = "error" if n >= PARTS_ERROR else "warn" if n >= PARTS_WARN else "ok"
        add("parts", "Parts pr. partition", status, f"Højst {n} aktive parts i én partition")

    async def mutations() -> None:
        row = (await clickhouse.query(MUTATIONS_CHECK_SQL, {"minutes": MUTATION_STUCK_MINUTES}))[0]
        failing, stuck = int(row["failing"]), int(row["stuck"])
        if failing:
            add("mutations", "Mutationer", "warn", f"{failing} mutation(er) fejler")
        elif stuck:
            add("mutations", "Mutationer", "warn", f"{stuck} mutation(er) har kørt i over {MUTATION_STUCK_MINUTES} min")
        else:
            add("mutations", "Mutationer", "ok", "Ingen fejlende eller hængende mutationer")

    async def migrations() -> None:
        pending = await clickhouse_migrate.pending()
        if pending:
            add("migrations", "Skema", "warn", f"Mangler: {', '.join(pending)} (kør python -m app.migrate)")
        else:
            add("migrations", "Skema", "ok", "Alle ClickHouse-migrationer er kørt")

    await guarded("disk", "Diskplads", disks)
    await guarded("parts", "Parts pr. partition", parts)
    await guarded("mutations", "Mutationer", mutations)
    await guarded("migrations", "Skema", migrations)
    status = max((c["status"] for c in checks), key=_RANK.__getitem__)
    return {"status": status, "version": server["version"], "checks": checks}


async def record(result: dict) -> dict:
    """Stores one run; on a change of status, audit + webhook in the same transaction. Returns the new row."""
    async with db.connection() as conn:
        prev = await db.fetch_one(conn, "select status from clickhouse_checks order by id desc limit 1")
        row = await db.fetch_one(conn, """
            insert into clickhouse_checks (status, version, checks) values (%s, %s, %s)
            returning id, checked_at, status""", (result["status"], result["version"], Jsonb(result["checks"])))
        previous = prev["status"] if prev else None
        # The first run only counts as a change when it is not ok.
        if previous != result["status"] and (previous is not None or result["status"] != "ok"):
            await audit(conn, "clickhouse.status", actor_id=None, meta=RequestMeta(), table="clickhouse_checks",
                        row_id=row["id"], status=result["status"], previous=previous)
            await webhooks.emit(conn, "clickhouse.status.changed",
                                {"check_id": row["id"], "status": result["status"], "previous": previous})
    return {**row, "previous": previous}


@jobs.handler("clickhouse.monitor")
async def monitor_job(payload: dict, job: dict) -> dict:
    if not clickhouse.configured():
        return {"status": "off"}
    result = await check()
    row = await record(result)
    if result["status"] != "ok":
        log.warning("clickhouse %s: %s", result["status"],
                    "; ".join(c["detail"] for c in result["checks"] if c["status"] != "ok"))
    return {"status": result["status"], "check_id": row["id"], "previous": row["previous"]}


async def overview() -> dict:
    """Server, disks, metrics, the largest tables and pending schema files, fetched side by side."""
    results = await asyncio.gather(
        clickhouse.query(SERVER_SQL), clickhouse.query(DISKS_SQL), clickhouse.query(METRICS_SQL),
        clickhouse.query(ASYNC_METRICS_SQL), clickhouse.query(TABLES_SQL, {"limit": 20}),
        clickhouse_migrate.pending(), return_exceptions=True)
    server, disks, metrics, async_metrics, tables, pending = results
    if isinstance(server, BaseException):
        raise server
    part = lambda v: None if isinstance(v, BaseException) else v  # noqa: E731
    return {
        "server": {**server[0], "uptime": duration(server[0]["uptime_seconds"])} if server else None,
        "disks": part(disks),
        "metrics": {r["metric"]: r["value"] for r in (part(metrics) or []) + (part(async_metrics) or [])},
        "tables": part(tables),
        "pending_migrations": part(pending),
    }
