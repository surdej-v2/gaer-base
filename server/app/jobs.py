"""Background jobs: a queue in Postgres, workers in the app process and a scheduler for recurring jobs.

    await jobs.enqueue(conn, "webhook.deliver", {"delivery_id": "…"})   # in the same transaction as the change

    @jobs.handler("my.kind")
    async def my_job(payload: dict, job: dict) -> dict | None: ...      # return value is stored as the result

Workers take the next ready job with `select … for update skip locked`, so any number of app containers can run
side by side without running a job twice. A job that raises is retried with exponential backoff (30 s, 1 min, 2 min …,
at most 1 hour) until max_attempts, then it is 'dead'. A job left 'running' longer than JOB_TIMEOUT_SECONDS (the
container died) is put back in the queue. Recurring jobs are rows in job_schedules; /admin/jobs can pause them,
change the interval and run them now.

Started from app.main when RUN_MAINTENANCE=1 (off in tests, where run_pending() drives the queue).
"""

import asyncio
import json
import logging
import os
import socket
import traceback
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from psycopg.types.json import Jsonb

from app import config, db

log = logging.getLogger("gaer.jobs")

Handler = Callable[[dict, dict], Awaitable[dict | None]]
HANDLERS: dict[str, Handler] = {}
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class Retry(Exception):
    """Raise to retry later without counting it as an error in the log (e.g. a receiver answered 503)."""


def handler(kind: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return register


def backoff_seconds(attempts: int) -> int:
    return min(3600, 30 * 2 ** max(0, attempts - 1))


async def enqueue(conn, kind: str, payload: dict | None = None, *, run_in_seconds: int = 0, priority: int = 0,
                  max_attempts: int = 5, dedupe_key: str | None = None, schedule: str | None = None) -> int | None:
    """Adds a job. With dedupe_key, nothing is added while a job with that key is queued or running (returns None)."""
    row = await db.fetch_one(conn, """
        insert into jobs (kind, payload, run_at, priority, max_attempts, dedupe_key, schedule)
        values (%s, %s, now() + make_interval(secs => %s), %s, %s, %s, %s)
        on conflict (dedupe_key) where dedupe_key is not null and status in ('queued', 'running') do nothing
        returning id""", (kind, Jsonb(payload or {}), run_in_seconds, priority, max_attempts, dedupe_key, schedule))
    return row["id"] if row else None


async def _claim(conn) -> dict | None:
    return await db.fetch_one(conn, """
        update jobs set status = 'running', locked_by = %s, locked_at = now(), started_at = now(),
                        attempts = attempts + 1
        where id = (select id from jobs where status = 'queued' and run_at <= now()
                    order by priority desc, run_at, id limit 1 for update skip locked)
        returning *""", (WORKER_ID,))


async def run_one() -> bool:
    """Runs the next ready job, if any. Returns whether a job was run."""
    from app import config_store
    await config_store.refresh()  # /admin/config, as for requests
    async with db.connection() as conn:
        job = await _claim(conn)
    if job is None:
        return False
    fn = HANDLERS.get(job["kind"])
    try:
        if fn is None:
            raise RuntimeError(f"no handler for {job['kind']}")
        result = await asyncio.wait_for(fn(job["payload"], job), timeout=config.settings.job_timeout_seconds)
    except Exception as e:  # noqa: BLE001 — every failure is recorded on the job
        retry = isinstance(e, Retry)
        if not retry:
            log.warning("job %s (%s) failed: %s", job["id"], job["kind"], e)
            from app import events
            await events.emit("job.failed", ref=job["kind"])
        error = str(e) if retry else "".join(traceback.format_exception_only(e)).strip()
        dead = job["attempts"] >= job["max_attempts"] or fn is None
        async with db.connection() as conn:
            await conn.execute("""
                update jobs set status = %s, last_error = %s, locked_by = null, locked_at = null,
                                run_at = now() + make_interval(secs => %s),
                                finished_at = case when %s then now() end
                where id = %s""", ("dead" if dead else "queued", error[:2000], backoff_seconds(job["attempts"]),
                                   dead, job["id"]))
            if dead and job["kind"] in DEAD_HOOKS:
                await DEAD_HOOKS[job["kind"]](conn, job, error)
        return True
    async with db.connection() as conn:
        await conn.execute("""update jobs set status = 'succeeded', result = %s, last_error = null, locked_by = null,
                                              locked_at = null, finished_at = now() where id = %s""",
                           (Jsonb(json.loads(json.dumps(result, default=str))) if result is not None else None,
                            job["id"]))
    return True


# Called in the same transaction when a job gives up (kind → fn(conn, job, error)).
DEAD_HOOKS: dict[str, Callable[[object, dict, str], Awaitable[None]]] = {}


async def schedule_due() -> int:
    """Enqueues a job for each enabled schedule whose time has come. Returns how many."""
    n = 0
    async with db.connection() as conn:
        due = await db.fetch_all(conn, """
            select * from job_schedules where enabled and next_run_at <= now()
            for update skip locked""")
        for s in due:
            job_id = await enqueue(conn, s["kind"], s["payload"], dedupe_key=f"schedule:{s['name']}",
                                   schedule=s["name"], max_attempts=3)
            await conn.execute("""update job_schedules set next_run_at = now() + make_interval(secs => interval_seconds),
                                         last_run_at = now(), last_job_id = coalesce(%s, last_job_id)
                                  where name = %s""", (job_id, s["name"]))
            n += 1
    return n


async def requeue_stale() -> int:
    async with db.connection() as conn:
        cur = await conn.execute("""
            update jobs set status = case when attempts >= max_attempts then 'dead' else 'queued' end,
                            last_error = 'Workeren stoppede midt i jobbet', locked_by = null, locked_at = null,
                            finished_at = case when attempts >= max_attempts then now() end
            where status = 'running' and locked_at < now() - make_interval(secs => %s)""",
            (config.settings.job_timeout_seconds + 60,))
        return cur.rowcount


async def run_pending(limit: int = 100) -> int:
    """Runs ready jobs until none are left (tests, and "Kør nu" for one job). Returns how many ran."""
    n = 0
    while n < limit and await run_one():
        n += 1
    return n


# ── The loops started by app.main ───────────────────────────────────────────

async def _worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if await run_one():
                continue
        except Exception:
            log.exception("job worker")
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.settings.job_poll_seconds)
        except TimeoutError:
            pass


async def _scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await schedule_due()
        except Exception:
            log.exception("job scheduler")
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


def start() -> tuple[asyncio.Event, list[asyncio.Task]]:
    stop = asyncio.Event()
    tasks = [asyncio.create_task(_scheduler(stop))]
    tasks += [asyncio.create_task(_worker(stop)) for _ in range(max(1, config.settings.job_workers))]
    log.info("job workers started: %s × %s", config.settings.job_workers, WORKER_ID)
    return stop, tasks


async def stop_all(stop: asyncio.Event, tasks: list[asyncio.Task]) -> None:
    stop.set()
    await asyncio.wait(tasks, timeout=10)
    for t in tasks:
        t.cancel()


# ── Built-in jobs ───────────────────────────────────────────────────────────

@handler("maintenance.hourly")
async def maintenance_hourly(payload: dict, job: dict) -> dict:
    async with db.connection() as conn:
        today = date.today()
        await conn.execute("select gaer_ensure_analytics_partition(%s)", (today,))
        await conn.execute("select gaer_ensure_analytics_partition(%s)", (today.replace(day=1) + timedelta(days=32),))
    return {"stale_jobs_requeued": await requeue_stale()}


@handler("retention.logs")
async def retention_logs(payload: dict, job: dict) -> dict:
    from app import retention
    from app.logs import RequestMeta
    return await retention.purge_old(actor_id=None, meta=RequestMeta(), reason="retention")


@handler("retention.cleanup")
async def retention_cleanup(payload: dict, job: dict) -> dict:
    from app import retention
    return await retention.cleanup(only=payload.get("only"))


@handler("noop")
async def noop(payload: dict, job: dict) -> dict:
    """For trying the queue from /admin/jobs."""
    if payload.get("fail"):
        raise RuntimeError("fejlede med vilje")
    return {"ok": True}
