"""Events: short-lived facts the app counts, for /admin/resources and for rules that close a kill switch.

    await events.emit("http.5xx", ref="/api/files/{file_id}")
    await events.emit("ai.tokens", ref="app", value=812)

The path of an event:
- With NATS (NATS_URL): emit() publishes to `events.<kind>`; the JetStream stream EVENTS keeps every event for 7 days
  (at most EVENTS_STREAM_MAX_MB), so it can be replayed. The durable consumer "store" writes them to Postgres in
  batches (store_messages). Several containers share the consumer; each message is stored once (id is the key).
- Without NATS, or while it is unreachable: emit() writes to Postgres directly. Nothing is lost, it is just slower.
- Postgres keeps them 7 days (retention policy "events").

Rules (setting kill_rules, edited in /admin/resources): "more than N events of a kind within S seconds → close
switch X for M minutes". evaluate_rules() counts in Postgres every EVENTS_RULES_SECONDS, so the count is right across
containers; closing is idempotent, audited and sent as the webhook event killswitch.changed.

No personal data: ref is an object id or a route, never a user id, e-mail, phone number or IP; data holds ids and
numbers. emit() never raises: counting must not break the request that is being counted.

start()/stop_all() are called by app.main's lifespan, like app.jobs: they connect to NATS in every process, and with
RUN_MAINTENANCE also run the store consumer and the rule check.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from app import config, db, killswitch, settings_store, webhooks
from app.logs import RequestMeta, audit

log = logging.getLogger("gaer.events")

STREAM = "EVENTS"
SUBJECT_PREFIX = "events."
DURABLE = "store"
TTL_DAYS = 7
KIND = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,3}$")

# The kinds the app emits today, for /admin/resources (others may be added by a fork; they show without a text).
KINDS: dict[str, str] = {
    "http.5xx": "Serverfejl (5xx) pr. rute",
    "http.429": "For mange forespørgsler (429) pr. rute",
    "http.blocked": "Forespørgsler afvist af et nødstop",
    "auth.otp_sent": "Engangskoder sendt",
    "auth.otp_failed": "Forkerte engangskoder",
    "ai.tokens": "AI-tokens pr. svar (værdi = tokens)",
    "job.failed": "Baggrundsjob der fejlede (ref = type)",
    "meta.node_created": "Noder oprettet i træstrukturer (ref = node-id)",
    "mapper.import": "Import af favoritter til Mapper (ref = rodens node-id, værdi = genveje lagt i mapper)",
}

_nc = None          # nats.aio.client.Client once connected
_js = None


def bus() -> str:
    return "nats" if _nc is not None and _nc.is_connected else "postgres"


def _event(kind: str, ref: str, value: float, data: dict | None) -> dict:
    return {"id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(), "kind": kind,
            "ref": str(ref)[:200], "value": float(value), "data": data or {}}


async def _insert(rows: list[dict]) -> int:
    if not rows:
        return 0
    async with db.connection() as conn:
        cur = await conn.execute(
            """insert into events (id, ts, kind, ref, value, data)
               select * from unnest(%s::uuid[], %s::timestamptz[], %s::text[], %s::text[], %s::float8[], %s::jsonb[])
               on conflict (id) do nothing""",
            ([r["id"] for r in rows], [r["ts"] for r in rows], [r["kind"] for r in rows], [r["ref"] for r in rows],
             [r["value"] for r in rows], [Jsonb(r["data"]) for r in rows]))
        for listener in LISTENERS:
            await listener(conn, rows)
        return cur.rowcount


# Code that wants to act on stored events in the same transaction (flows: a trigger on an event kind): fn(conn, rows).
# A listener must never raise.
LISTENERS: list[Callable[[object, list[dict]], Awaitable[None]]] = []


async def emit(kind: str, *, ref: str = "", value: float = 0, data: dict | None = None) -> bool:
    """Record one event. Returns False (and logs) for an invalid kind or when it could not be recorded at all."""
    if not KIND.match(kind):
        log.warning("event with invalid kind %r ignored", kind)
        return False
    event = _event(kind, ref, value, data)
    if _nc is not None and _nc.is_connected:
        try:
            await _nc.publish(SUBJECT_PREFIX + kind, json.dumps(event).encode())
            return True
        except Exception as e:  # noqa: BLE001 — fall back to Postgres
            log.warning("event %s not published to NATS, writing to Postgres: %s", kind, e)
    try:
        await _insert([event])
        return True
    except Exception:  # noqa: BLE001
        log.exception("event %s lost", kind)
        return False


async def store_messages(msgs: list) -> int:
    """The consumer's work: parse a batch from JetStream, store it, then ack. A bad message is acked and dropped."""
    rows = []
    for m in msgs:
        try:
            e = json.loads(m.data)
            if KIND.match(e.get("kind", "")):
                rows.append({"id": str(uuid.UUID(e["id"])), "ts": e["ts"], "kind": e["kind"], "ref": str(e.get("ref", ""))[:200],
                             "value": float(e.get("value") or 0), "data": e.get("data") or {}})
        except (ValueError, KeyError, TypeError):
            log.warning("unreadable event dropped")
    n = await _insert(rows)
    for m in msgs:
        await m.ack()
    return n


# ── Rules ───────────────────────────────────────────────────────────────────

async def set_switch(conn, key: str, active: bool, *, reason: str = "", minutes: int | None = None,
                     actor_id: int | None = None, meta: RequestMeta | None = None, auto: bool = False) -> bool:
    """Close or open a switch in the caller's transaction, with audit and webhook. Returns whether it changed."""
    before = await db.fetch_one(conn, "select active, reason, auto, expires_at from kill_switches where key = %s", (key,))
    was_active = bool(before and before["active"] and (before["expires_at"] is None
                                                       or before["expires_at"] > datetime.now(timezone.utc)))
    if auto and was_active:
        return False
    if not active and not was_active and not (before and before["active"]):
        return False
    await conn.execute(
        """insert into kill_switches (key, active, reason, auto, set_by, set_at, expires_at)
           values (%(k)s, %(a)s, %(r)s, %(auto)s, %(by)s, now(),
                   case when %(m)s::int is null then null else now() + make_interval(mins => %(m)s::int) end)
           on conflict (key) do update set active = excluded.active, reason = excluded.reason, auto = excluded.auto,
               set_by = excluded.set_by, set_at = excluded.set_at, expires_at = excluded.expires_at""",
        {"k": key, "a": active, "r": reason[:500], "auto": auto, "by": actor_id, "m": minutes if active else None})
    await audit(conn, "killswitch.auto" if auto else "update", actor_id=actor_id, meta=meta or RequestMeta(),
                table="kill_switches", row_id=key,
                before={"active": was_active, "reason": before["reason"] if before else ""},
                after={"active": active, "reason": reason, "minutes": minutes, "auto": auto})
    await webhooks.emit(conn, "killswitch.changed", {"key": key, "active": active, "auto": auto})
    return True


async def evaluate_rules() -> list[str]:
    """Close the switches whose rule is over its threshold. Returns the keys that were closed now."""
    async with db.connection() as conn:
        rules = await settings_store.get(conn, "kill_rules") or []
        closed = []
        for rule in rules:
            if not rule.get("enabled", True) or rule.get("switch") not in killswitch.SWITCHES:
                continue
            row = await db.fetch_one(conn, """select count(*) as n from events
                                               where kind = %s and ts > now() - make_interval(secs => %s)""",
                                     (rule["kind"], int(rule["window_seconds"])))
            if row["n"] < int(rule["threshold"]):
                continue
            reason = f"Automatisk: {rule['kind']} {row['n']} gange på {rule['window_seconds']} sek."
            if await set_switch(conn, rule["switch"], True, reason=reason, minutes=rule.get("minutes"), auto=True):
                closed.append(rule["switch"])
    if closed:
        killswitch.invalidate()
        log.warning("kill switch closed by rule: %s", ", ".join(closed))
    return closed


# ── The loops started by app.main ───────────────────────────────────────────

async def _connect() -> None:
    global _nc, _js
    import nats
    from nats.js.api import DiscardPolicy, StorageType, StreamConfig
    from nats.js.errors import BadRequestError

    nc = await nats.connect(config.settings.nats_url, name="gaer-app", connect_timeout=3, max_reconnect_attempts=-1,
                            reconnect_time_wait=2)
    js = nc.jetstream()
    cfg = StreamConfig(name=STREAM, subjects=[SUBJECT_PREFIX + ">"], max_age=TTL_DAYS * 86400,
                       max_bytes=config.settings.events_stream_max_mb * 1024 * 1024,
                       storage=StorageType.FILE, discard=DiscardPolicy.OLD)
    try:
        await js.add_stream(cfg)
    except BadRequestError:
        await js.update_stream(cfg)
    _nc, _js = nc, js
    log.info("events: connected to NATS, stream %s", STREAM)


async def _connector(stop: asyncio.Event) -> None:
    while not stop.is_set() and _nc is None:
        try:
            await _connect()
            return
        except Exception as e:  # noqa: BLE001
            log.warning("events: NATS not reachable (%s), writing to Postgres until it is", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass


async def _store(stop: asyncio.Event) -> None:
    from nats.errors import TimeoutError as NatsTimeout
    sub = None
    while not stop.is_set():
        try:
            if _js is None:
                await asyncio.wait_for(stop.wait(), timeout=2)
                continue
            if sub is None:
                sub = await _js.pull_subscribe(SUBJECT_PREFIX + ">", durable=DURABLE, stream=STREAM)
            try:
                msgs = await sub.fetch(500, timeout=1)
            except NatsTimeout:
                continue
            await store_messages(msgs)
        except TimeoutError:
            continue
        except Exception:
            log.exception("events: store consumer")
            sub = None
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass


async def _rules(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await evaluate_rules()
        except Exception:
            log.exception("events: rules")
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.settings.events_rules_seconds)
        except TimeoutError:
            pass


def start(maintenance: bool) -> tuple[asyncio.Event, list[asyncio.Task]]:
    stop = asyncio.Event()
    tasks = []
    if config.settings.nats_url:
        tasks.append(asyncio.create_task(_connector(stop)))
        if maintenance:
            tasks.append(asyncio.create_task(_store(stop)))
    if maintenance:
        tasks.append(asyncio.create_task(_rules(stop)))
    return stop, tasks


async def stop_all(stop: asyncio.Event, tasks: list[asyncio.Task]) -> None:
    global _nc, _js
    stop.set()
    if tasks:
        await asyncio.wait(tasks, timeout=10)
    for t in tasks:
        t.cancel()
    if _nc is not None:
        try:
            await _nc.drain()
        except Exception:  # noqa: BLE001
            pass
    _nc = _js = None
