"""ClickHouse over its HTTP interface (CLICKHOUSE_URL, port 8123 in the stack). No driver: httpx is enough.

    rows = await clickhouse.query("select kind, count() as n from events where ts > now() - toIntervalDay({days:UInt32})"
                                  " group by kind", {"days": 7})
    await clickhouse.insert("events", [{"kind": "clip.saved", "source": "app", "ref": "clip:12"}])
    await clickhouse.execute("optimize table events final")

Values are always sent as query parameters ({name:Type} in the SQL, param_<name> on the wire), never formatted into
the SQL. Table names come from the code, not from requests. The module is off without CLICKHOUSE_URL: configured()
says so and every call raises ClickHouseError(503).

ClickHouse holds no personal data in Gær: the events table carries ids of things (ref), never of people. A table that
needs personal data must be added to the GDPR export and erasure in app.gdpr first.
"""

import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from app import config

# Replaced in tests with an httpx.MockTransport (async) / sync_transport (httpx.MockTransport for migrations).
transport: httpx.AsyncBaseTransport | None = None
sync_transport: httpx.BaseTransport | None = None

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_CODE = re.compile(r"^Code:\s*(\d+)\.\s*(?:DB::Exception:\s*)?")


class ClickHouseError(Exception):
    def __init__(self, status: int, message: str, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def configured() -> bool:
    return bool(config.settings.clickhouse_url)


def redact_url(url: str | None) -> str | None:
    """http://user:secret@clickhouse:8123 → http://••••@clickhouse:8123 (never show credentials in /admin)."""
    if not url:
        return None
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, f"••••@{host}", parts.path, parts.query, ""))
    return url


def identifier(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"not a ClickHouse table name: {name!r}")
    return name


def _value(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _prepare(sql: str | None, params: dict | None, settings: dict | None,
             database: str | None = None) -> tuple[str, dict, dict]:
    s = config.settings
    if not s.clickhouse_url:
        raise ClickHouseError(503, "ClickHouse er ikke sat op (CLICKHOUSE_URL mangler)")
    query: dict[str, str] = {"database": database or s.clickhouse_database,
                             "output_format_json_quote_64bit_integers": "0"}
    if sql is not None:
        query["query"] = sql
    for k, v in (params or {}).items():
        query[f"param_{k}"] = _value(v)
    for k, v in (settings or {}).items():
        query[k] = _value(v)
    headers = {"X-ClickHouse-User": s.clickhouse_user, "X-ClickHouse-Key": s.clickhouse_password}
    return s.clickhouse_url.rstrip("/") + "/", query, headers


def _error(r: httpx.Response) -> ClickHouseError:
    text = r.text.strip().splitlines()[0] if r.text.strip() else f"HTTP {r.status_code}"
    m = _CODE.match(text)
    code = int(m.group(1)) if m else None
    message = _CODE.sub("", text)
    # "… (version 26.8.36.3 (official build))" adds nothing in /admin.
    message = re.sub(r"\s*\(version [^)]*\)+\s*$", "", message)[:500]
    status = 400 if r.status_code < 500 and code is not None else 502
    return ClickHouseError(status, f"ClickHouse: {message}", code)


def _rows(r: httpx.Response) -> list[dict]:
    try:
        data = r.json()
    except ValueError:
        raise ClickHouseError(502, "ClickHouse svarede ikke med JSON")
    return data.get("data", []) if isinstance(data, dict) else []


def _failed(e: httpx.HTTPError) -> ClickHouseError:
    return ClickHouseError(502, f"ClickHouse svarer ikke ({type(e).__name__})")


async def _post(sql: str | None, params: dict | None, settings: dict | None, body: bytes | None = None) -> httpx.Response:
    url, query, headers = _prepare(sql, params, settings)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=config.settings.clickhouse_timeout) as client:
            r = await client.post(url, params=query, headers=headers, content=body or b"")
    except httpx.HTTPError as e:
        raise _failed(e)
    if r.status_code >= 400:
        raise _error(r)
    return r


async def query(sql: str, params: dict | None = None, *, settings: dict | None = None) -> list[dict]:
    """A SELECT; returns the rows as dicts. Do not end the SQL with FORMAT, it is added here."""
    r = await _post(f"{sql.rstrip().rstrip(';')}\nFORMAT JSON", params, settings)
    return _rows(r)


async def execute(sql: str, params: dict | None = None, *, settings: dict | None = None) -> None:
    """DDL, mutations and other statements without a result."""
    await _post(sql.rstrip().rstrip(";"), params, settings)


async def insert(table: str, rows: list[dict]) -> int:
    """INSERT … FORMAT JSONEachRow. Columns left out get their defaults. Returns the number of rows sent."""
    if not rows:
        return 0
    body = "\n".join(json.dumps(row, default=str, ensure_ascii=False) for row in rows).encode()
    await _post(f"INSERT INTO {identifier(table)} FORMAT JSONEachRow", None, None, body)
    return len(rows)


async def ping() -> bool:
    url, _, _ = _prepare(None, None, None)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=config.settings.clickhouse_timeout) as client:
            r = await client.get(url + "ping")
    except httpx.HTTPError as e:
        raise _failed(e)
    return r.status_code == 200


async def delete_older(table: str, column: str, days: int) -> int:
    """Deletes rows older than `days` (by `column`) from a MergeTree table and returns how many. The count is taken
    first, so a table with nothing to delete is never mutated. mutations_sync=1 waits for the delete to finish."""
    t, c = identifier(table), identifier(column)
    where = f"{c} < now() - toIntervalDay({{days:UInt32}})"
    rows = await query(f"SELECT count() AS n FROM {t} WHERE {where}", {"days": days})
    n = int(rows[0]["n"]) if rows else 0
    if n:
        await execute(f"ALTER TABLE {t} DELETE WHERE {where}", {"days": days}, settings={"mutations_sync": 1})
    return n


# ── Synchronous calls, for python -m app.migrate (which is not async) ───────

def execute_sync(sql: str, params: dict | None = None, *, database: str | None = None) -> None:
    """database: run in another database than CLICKHOUSE_DATABASE ("system" for CREATE DATABASE)."""
    _post_sync(sql.rstrip().rstrip(";"), params, database)


def query_sync(sql: str, params: dict | None = None) -> list[dict]:
    return _rows(_post_sync(f"{sql.rstrip().rstrip(';')}\nFORMAT JSON", params))


def _post_sync(sql: str, params: dict | None, database: str | None = None) -> httpx.Response:
    url, query_params, headers = _prepare(sql, params, None, database)
    try:
        with httpx.Client(transport=sync_transport, timeout=config.settings.clickhouse_timeout) as client:
            r = client.post(url, params=query_params, headers=headers)
    except httpx.HTTPError as e:
        raise _failed(e)
    if r.status_code >= 400:
        raise _error(r)
    return r
