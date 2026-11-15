"""Connection pools. The app pool uses the app user; the purge pool uses the separate purge user."""

from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app import config

_app_pool: AsyncConnectionPool | None = None
_purge_pool: AsyncConnectionPool | None = None
_analytics_pool: AsyncConnectionPool | None = None
_pipeline_pool: AsyncConnectionPool | None = None


async def open_pools() -> None:
    global _app_pool, _purge_pool
    s = config.settings
    _app_pool = AsyncConnectionPool(s.database_url, min_size=1, max_size=10, open=False,
                                    kwargs={"row_factory": dict_row, "autocommit": False})
    await _app_pool.open(wait=True)
    if s.purge_database_url:
        _purge_pool = AsyncConnectionPool(s.purge_database_url, min_size=0, max_size=2, open=False,
                                          kwargs={"row_factory": dict_row, "autocommit": False})
        await _purge_pool.open()
    global _analytics_pool, _pipeline_pool
    _analytics_pool = await _optional_pool(s.analytics_database_url)
    _pipeline_pool = await _optional_pool(s.pipeline_database_url)


async def _optional_pool(url: str | None) -> AsyncConnectionPool | None:
    if not url:
        return None
    pool = AsyncConnectionPool(url, min_size=0, max_size=5, open=False,
                               kwargs={"row_factory": dict_row, "autocommit": False})
    await pool.open()
    return pool


async def close_pools() -> None:
    global _app_pool, _purge_pool, _analytics_pool, _pipeline_pool
    for pool in (_app_pool, _purge_pool, _analytics_pool, _pipeline_pool):
        if pool is not None:
            await pool.close()
    _app_pool = _purge_pool = _analytics_pool = _pipeline_pool = None


@asynccontextmanager
async def connection():
    """A transaction on the app user. Commits on success, rolls back on error."""
    assert _app_pool is not None, "database pool is not open"
    async with _app_pool.connection() as conn:
        yield conn


@asynccontextmanager
async def purge_connection():
    """A transaction on the purge user. Falls back to the app pool in development,
    where a single superuser typically owns everything."""
    pool = _purge_pool
    if pool is None:
        if config.settings.is_production:
            raise RuntimeError("PURGE_DATABASE_URL is required in production")
        pool = _app_pool
    assert pool is not None
    async with pool.connection() as conn:
        yield conn


@asynccontextmanager
async def analytics_connection():
    """A transaction where snapshots and foreign tables live (ANALYTICS_DATABASE_URL; default the app's database).
    Runtime reads snapshots with it."""
    pool = _analytics_pool or _app_pool
    assert pool is not None, "database pool is not open"
    async with pool.connection() as conn:
        yield conn


@asynccontextmanager
async def pipeline_connection():
    """The role pipelines build snapshots with (PIPELINE_DATABASE_URL; default the analytics connection)."""
    pool = _pipeline_pool or _analytics_pool or _app_pool
    assert pool is not None, "database pool is not open"
    async with pool.connection() as conn:
        yield conn


def analytics_is_separate() -> bool:
    return _analytics_pool is not None


async def fetch_one(conn, sql: str, params=None) -> dict | None:
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def fetch_all(conn, sql: str, params=None) -> list[dict]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()
