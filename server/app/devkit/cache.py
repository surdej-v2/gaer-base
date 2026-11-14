"""Cache, rate limits and idempotency for the runtime (F-17 … F-21, MCP-15, API-4, API-11, API-12).

One interface, two stores: Redis when REDIS_URL is set, otherwise Postgres (dev_cache, dev_rate_buckets,
dev_idempotency). The app never requires Redis to start; when Redis cannot be reached the call falls back to Postgres.

- **Keys** (F-21): artifact, version, step, a hash of the parameters, a generation and — for delegated sources — the
  user (F-19). The generation is per scope (`dev_cache_generations`, or a dataset's `generation` for snapshots), so
  "Ryd cache", a new version and a new refresh make old entries unreachable at once; they then expire by TTL.
- **Values** (F-20) are encrypted in the application with AES-256-GCM (app/secrets_box.py, the instance's key
  provider) before they are written. Only masked rows ever reach the cache: the runtime masks first.
- **TTL** is enforced by Redis itself, or by `expires_at` in Postgres. The cleanup job removes expired entries and,
  above DEV_CACHE_MAX_ENTRIES, the least recently used first (F-20; for Redis set `maxmemory-policy allkeys-lru`).
- **Stale-while-revalidate** (F-21): an entry past its TTL but younger than the owner's max age is returned as
  "stale" and the caller refreshes it.
- **Rate limits**: a token bucket per key (rate per minute, burst = rate), atomic in Redis (Lua) or in one Postgres
  statement. Denied → (False, seconds until a token is back) for HTTP 429 / Retry-After.
"""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from app import config, db, jobs, secrets_box

PURPOSE = "developer-cache"


# ── A small Redis client (RESP2). Only what the cache needs; no dependency. ──

class RedisError(Exception):
    pass


class Redis:
    def __init__(self, url: str):
        u = urlparse(url)
        self.host, self.port = u.hostname or "localhost", u.port or 6379
        self.password = u.password
        self.db = int((u.path or "/0").lstrip("/") or 0)
        self.tls = u.scheme == "rediss"
        self._lock = asyncio.Lock()
        self._conn: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None

    async def _connect(self):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port, ssl=self.tls or None), 2)
        self._conn = (reader, writer)
        if self.password:
            await self._raw("AUTH", self.password)
        if self.db:
            await self._raw("SELECT", str(self.db))

    @staticmethod
    def _encode(*args) -> bytes:
        out = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = a if isinstance(a, bytes) else str(a).encode()
            out.append(f"${len(b)}\r\n".encode() + b + b"\r\n")
        return b"".join(out)

    async def _read(self, reader):
        line = await reader.readline()
        if not line:
            raise RedisError("forbindelsen lukkede")
        kind, rest = line[:1], line[1:-2]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RedisError(rest.decode())
        if kind == b":":
            return int(rest)
        if kind == b"$":
            n = int(rest)
            if n < 0:
                return None
            data = await reader.readexactly(n + 2)
            return data[:-2]
        if kind == b"*":
            n = int(rest)
            return None if n < 0 else [await self._read(reader) for _ in range(n)]
        raise RedisError("ukendt svar")

    async def _raw(self, *args):
        reader, writer = self._conn
        writer.write(self._encode(*args))
        await writer.drain()
        return await asyncio.wait_for(self._read(reader), 2)

    async def call(self, *args):
        async with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        await self._connect()
                    return await self._raw(*args)
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, RedisError) as e:
                    self._conn = None
                    if attempt == 2 or isinstance(e, RedisError) and "forbindelsen" not in str(e):
                        raise RedisError(str(e) or type(e).__name__)


_redis: Redis | None = None
_redis_url: str | None = None


def redis() -> Redis | None:
    global _redis, _redis_url
    url = config.settings.redis_url
    if not url:
        return None
    if _redis is None or _redis_url != url:
        _redis, _redis_url = Redis(url), url
    return _redis


def backend() -> str:
    return "redis" if config.settings.redis_url else "postgres"


# ── Keys and generations ────────────────────────────────────────────────────

def params_hash(params: dict | None) -> str:
    return hashlib.sha256(json.dumps(params or {}, sort_keys=True, default=str).encode()).hexdigest()[:24]


async def generation(scope: str) -> int:
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select generation from dev_cache_generations where scope = %s", (scope,))
    return row["generation"] if row else 0


async def bump(scope: str) -> int:
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """insert into dev_cache_generations (scope, generation) values (%s, 1)
                                          on conflict (scope) do update set generation = dev_cache_generations.generation + 1
                                          returning generation""", (scope,))
    return row["generation"]


def make_key(*, artifact_id: int, version: str, step: str, params: dict | None, gen: int | str, user_id: int | None = None) -> str:
    """F-19: a delegated source's entries carry the user; app-access entries are shared."""
    return f"gc:{artifact_id}:{version}:{step}:{params_hash(params)}:{gen}:{user_id if user_id is not None else '-'}"


# ── Values ──────────────────────────────────────────────────────────────────

def _seal(value) -> bytes:
    body = json.dumps({"v": value, "t": time.time()}, default=str, ensure_ascii=False)
    return secrets_box.encrypt(body, PURPOSE)


def _open(blob: bytes) -> tuple[object, float]:
    data = json.loads(secrets_box.decrypt(blob, PURPOSE))
    return data["v"], data["t"]


async def get(key: str, *, ttl_s: int, max_stale_s: int = 0) -> tuple[object | None, str, float | None]:
    """(value, "hit" | "stale" | "miss", age in seconds). Entries are stored for ttl + max_stale; past the TTL they are
    "stale" (stale-while-revalidate)."""
    blob = None
    r = redis()
    if r is not None:
        try:
            blob = await r.call("GET", key)
        except RedisError:
            r = None
    if r is None:
        async with db.connection() as conn:
            row = await db.fetch_one(conn, """update dev_cache set hits = hits + 1, last_hit_at = now()
                                              where key = %s and expires_at > now() returning value""", (key,))
        blob = bytes(row["value"]) if row else None
    if blob is None:
        return None, "miss", None
    try:
        value, stored = _open(blob)
    except Exception:  # noqa: BLE001 — a key rotated away or a damaged entry is a miss
        return None, "miss", None
    age = max(0.0, time.time() - stored)
    if age <= ttl_s:
        return value, "hit", age
    if age <= ttl_s + max_stale_s:
        return value, "stale", age
    return None, "miss", age


async def put(key: str, value, *, ttl_s: int, max_stale_s: int = 0) -> None:
    if ttl_s <= 0:
        return
    life = ttl_s + max(0, max_stale_s)
    blob = _seal(value)
    r = redis()
    if r is not None:
        try:
            await r.call("SET", key, blob, "PX", str(life * 1000))
            return
        except RedisError:
            pass
    async with db.connection() as conn:
        await conn.execute("""insert into dev_cache (key, value, stored_at, expires_at) values (%s, %s, now(), now() + make_interval(secs => %s))
                              on conflict (key) do update set value = excluded.value, stored_at = now(),
                              expires_at = excluded.expires_at, last_hit_at = now()""", (key, blob, life))


async def clear_artifact(artifact_id: int) -> int:
    """"Ryd cache": a new generation for the artifact; old entries are unreachable and expire."""
    return await bump(f"artifact:{artifact_id}")


async def clear_user(user_id: int) -> int:
    """F-19: when a user's delegated access is revoked, their entries go (extension point for Login-udbydere)."""
    async with db.connection() as conn:
        n = (await conn.execute("delete from dev_cache where key like %s", (f"%:{user_id}",))).rowcount
    r = redis()
    if r is not None:
        try:
            cursor = "0"
            while True:
                cursor, keys = await r.call("SCAN", cursor, "MATCH", f"gc:*:{user_id}", "COUNT", "500")
                cursor = cursor.decode() if isinstance(cursor, bytes) else str(cursor)
                if keys:
                    n += await r.call("DEL", *keys)
                if cursor == "0":
                    break
        except RedisError:
            pass
    return n


async def stats() -> dict:
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """select count(*) as entries, count(*) filter (where expires_at > now()) as live,
                                          coalesce(sum(length(value)), 0) as bytes, coalesce(sum(hits), 0) as hits from dev_cache""")
    return {"backend": backend(), **row, "max_entries": config.settings.dev_cache_max_entries}


# ── Rate limits (token bucket) ──────────────────────────────────────────────

LUA_BUCKET = """
local tokens = tonumber(redis.call('HGET', KEYS[1], 't') or ARGV[1])
local ts = tonumber(redis.call('HGET', KEYS[1], 'ts') or ARGV[3])
local cap, rate, now = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
tokens = math.min(cap, tokens + (now - ts) * rate)
local ok = 0
if tokens >= 1 then tokens = tokens - 1 ok = 1 end
redis.call('HSET', KEYS[1], 't', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate * 1000) + 1000)
return {ok, tostring(tokens)}
"""


async def take(key: str, per_minute: int) -> tuple[bool, int]:
    """One token from the bucket `key` (capacity = per_minute, refilled per_minute per minute).
    Returns (allowed, retry_after_seconds)."""
    if per_minute <= 0:
        return True, 0
    rate = per_minute / 60.0
    r = redis()
    if r is not None:
        try:
            ok, tokens = await r.call("EVAL", LUA_BUCKET, "1", f"rl:{key}", str(per_minute), str(rate), f"{time.time():.3f}")
            tokens = float(tokens)
            return bool(ok), 0 if ok else max(1, int((1 - tokens) / rate) + 1)
        except RedisError:
            pass
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """
            with cur as (select least(%(cap)s, coalesce((select tokens + extract(epoch from now() - updated_at) * %(rate)s
                                                          from dev_rate_buckets where key = %(key)s for update), %(cap)s)) as t)
            insert into dev_rate_buckets as b (key, tokens, updated_at)
            select %(key)s, case when t >= 1 then t - 1 else t end, now() from cur
            on conflict (key) do update set tokens = excluded.tokens, updated_at = now()
            returning tokens, (select t >= 1 from cur) as ok""", {"key": key, "cap": float(per_minute), "rate": rate})
    if row["ok"]:
        return True, 0
    return False, max(1, int((1 - row["tokens"]) / rate) + 1)


# ── Idempotency keys (API-4) ────────────────────────────────────────────────

async def idem_get(key: str) -> dict | None:
    r = redis()
    if r is not None:
        try:
            raw = await r.call("GET", f"idem:{key}")
            return json.loads(secrets_box.decrypt(raw, "developer-idempotency")) if raw else None
        except RedisError:
            pass
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select status, body from dev_idempotency where key = %s and expires_at > now()", (key,))
    return dict(row) if row else None


async def idem_put(key: str, status: int, body) -> bool:
    """Stores the answer for a day. False when another request stored one first."""
    r = redis()
    data = {"status": status, "body": body}
    if r is not None:
        try:
            blob = secrets_box.encrypt(json.dumps(data, default=str), "developer-idempotency")
            return (await r.call("SET", f"idem:{key}", blob, "NX", "PX", str(86_400_000))) == "OK"
        except RedisError:
            pass
    from psycopg.types.json import Jsonb
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """insert into dev_idempotency (key, status, body, expires_at) values (%s, %s, %s, now() + interval '1 day')
                                          on conflict (key) do nothing returning key""", (key, status, Jsonb(json.loads(json.dumps(body, default=str)))))
    return row is not None


# ── Cleanup ─────────────────────────────────────────────────────────────────

@jobs.handler("developer.cache_cleanup")
async def cleanup(payload: dict, job: dict) -> dict:
    async with db.connection() as conn:
        expired = (await conn.execute("delete from dev_cache where expires_at < now()")).rowcount
        over = await db.fetch_one(conn, "select count(*) as n from dev_cache")
        evicted = 0
        extra = over["n"] - config.settings.dev_cache_max_entries
        if extra > 0:
            evicted = (await conn.execute("""delete from dev_cache where key in (select key from dev_cache
                                             order by last_hit_at limit %s)""", (extra,))).rowcount
        buckets = (await conn.execute("delete from dev_rate_buckets where updated_at < now() - interval '1 day'")).rowcount
        idem = (await conn.execute("delete from dev_idempotency where expires_at < now()")).rowcount
    return {"expired": expired, "evicted": evicted, "buckets": buckets, "idempotency": idem,
            "at": datetime.now(timezone.utc).isoformat()}
