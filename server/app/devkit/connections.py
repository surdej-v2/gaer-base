"""Data connections (F-11, F-12, PL-5): databases through foreign data wrappers, or directly, and the connector
interface every type implements.

A connection is a row in `data_sources` (like the API sources from 0012; kinds postgres, mysql, graph, delegated, rest)
plus an artifact of type `connection` whose versions hold the definition. The definition never holds a secret — the
password is named as "gruppe/navn" and fetched through app/devkit/secrets.py for each sync or test.

    {"type": "postgres", "host": "db.example", "port": 5432, "database": "lejedata", "user": "laeser",
     "password": "data/lejedata-laeser", "schema": "public", "access": "fdw", "tables": ["lejemaal", "lejere"],
     "ingest_source": "mysql_backup_1"}

Access:
- `fdw` (postgres_fdw or mysql_fdw): one foreign server per connection (`gaer_conn_<key>`), a user mapping for the
  runtime and pipeline roles, and foreign tables only for the tables the definition lists, in the schema `fdw_<key>`
  of the analytics database. Pipelines join them in PostgreSQL; filters and field lists are pushed down by the FDW.
- `direct` (postgres only): the server connects for each query in a READ ONLY transaction with a short timeout. Used
  when postgres_fdw is not installed; operations and previews work, pipelines need FDW.
- MySQL needs mysql_fdw; when the database server does not have it, the connection says so and does nothing else.
- `delegated` is an extension point for live sources with the caller's own token (OAuth); it answers "ikke
  tilgængelig endnu" until that part is built.
"""

import re

import psycopg
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row

from app import config, db
from app.devkit import secrets as dev_secrets

TYPES = {"postgres": "PostgreSQL", "mysql": "MySQL", "rest": "REST (live)", "graph": "Microsoft Graph (live)",
         "delegated": "Delegeret adgang (live)"}
FDW = {"postgres": "postgres_fdw", "mysql": "mysql_fdw"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
NOT_YET = "Delegeret adgang (OAuth pr. bruger) er ikke tilgængelig endnu"


class ConnectionError_(HTTPException):
    def __init__(self, text: str, status: int = 424):
        super().__init__(status, text)


def schema_name(key: str) -> str:
    return "fdw_" + key.replace("-", "_")


def server_name(key: str) -> str:
    return "gaer_conn_" + key.replace("-", "_")


def parse(raw: dict) -> dict:
    """Validates a definition. Raises HTTPException(422) with a Danish message."""
    if not isinstance(raw, dict):
        raise HTTPException(422, "Definitionen skal være et objekt")
    kind = str(raw.get("type") or "").lower()
    if kind not in TYPES:
        raise HTTPException(422, "type skal være postgres, mysql, rest, graph eller delegated")
    if kind == "delegated":
        return {"type": kind, "provider": str(raw.get("provider") or "microsoft")[:40], "scopes": list(raw.get("scopes") or [])[:20],
                "tables": [], "ingest_source": None}
    if kind in ("rest", "graph"):
        # Live sources with app access are described in Forbindelser → Live-kilder (the API sources, app/data_sources.py).
        return {"type": kind, "source": str(raw.get("source") or "")[:40], "tables": [], "ingest_source": None}
    host = str(raw.get("host") or "").strip()
    if not HOST.match(host):
        raise HTTPException(422, "host mangler eller er ugyldig")
    try:
        port = int(raw.get("port") or (5432 if kind == "postgres" else 3306))
    except (TypeError, ValueError):
        raise HTTPException(422, "port skal være et tal")
    database = str(raw.get("database") or "").strip()
    user = str(raw.get("user") or "").strip()
    if not IDENT.match(database.replace("-", "_")) or not user or len(user) > 63:
        raise HTTPException(422, "database og user skal udfyldes")
    password = raw.get("password")
    if password and not dev_secrets.is_ref(password):
        raise HTTPException(422, "password skal være en hemmelighed: " + dev_secrets.REF_HELP + ". Læg aldrig en værdi her")
    schema = str(raw.get("schema") or ("public" if kind == "postgres" else database))
    if not IDENT.match(schema):
        raise HTTPException(422, "schema er ugyldigt")
    access = str(raw.get("access") or "fdw")
    if access not in ("fdw", "direct") or (kind == "mysql" and access != "fdw"):
        raise HTTPException(422, "access skal være fdw eller direct (MySQL kun fdw)")
    tables = raw.get("tables") or []
    if not isinstance(tables, list) or not all(isinstance(t, str) and IDENT.match(t) for t in tables) or len(tables) > 200:
        raise HTTPException(422, "tables skal være en liste af tabelnavne")
    ingest = raw.get("ingest_source")
    if ingest is not None and not re.match(r"^[a-z0-9_.-]{1,60}$", str(ingest)):
        raise HTTPException(422, "ingest_source er ugyldig")
    return {"type": kind, "host": host, "port": port, "database": database, "user": user, "password": password or None,
            "schema": schema, "access": access, "tables": sorted(set(tables)), "ingest_source": ingest,
            "sslmode": str(raw.get("sslmode") or "prefer")}


def contract(definition: dict) -> dict:
    # The tables a connection offers are its interface: a removed table breaks what reads it (major).
    return {"items": list(definition.get("tables") or [])}


def refs(definition: dict) -> list[tuple[str, str]]:
    return [(definition["password"], "auth")] if definition.get("password") else []


# ── Capabilities ────────────────────────────────────────────────────────────

async def capabilities() -> dict:
    async with db.analytics_connection() as conn:
        rows = await db.fetch_all(conn, """select a.name, e.extname is not null as installed from pg_available_extensions a
                                           left join pg_extension e on e.extname = a.name where a.name in ('postgres_fdw', 'mysql_fdw')""")
    have = {r["name"]: r["installed"] for r in rows}
    return {"postgres_fdw": bool(have.get("postgres_fdw")), "postgres_fdw_available": "postgres_fdw" in have,
            "mysql_fdw": bool(have.get("mysql_fdw")), "mysql_fdw_available": "mysql_fdw" in have,
            "analytics_separate": db.analytics_is_separate(), "ingest_table": config.settings.ingest_status_table}


async def fdw_problem(kind: str) -> str | None:
    caps = await capabilities()
    ext = FDW.get(kind)
    if ext and not caps[ext]:
        where = "findes ikke på databaseserveren" if not caps[f"{ext}_available"] else "er ikke installeret i databasen"
        return f"{ext} {where}. Bed driften installere den (CREATE EXTENSION {ext})"
    return None


# ── Secrets ─────────────────────────────────────────────────────────────────

async def password_for(conn, source: dict, definition: dict, *, tester: dict | None = None, meta=None) -> str | None:
    if not definition.get("password"):
        return None
    values, status = await dev_secrets.values(conn, key=source["key"], source_id=source["id"], refs=refs(definition),
                                              tester=tester, meta=meta)
    value = values.get(definition["password"])
    if not value:
        raise ConnectionError_(status[0]["text"] if status else "Hemmeligheden kan ikke bruges", 409)
    return value


# ── Direct access ───────────────────────────────────────────────────────────

async def direct(definition: dict, password: str | None) -> psycopg.AsyncConnection:
    if definition["type"] != "postgres":
        raise ConnectionError_("Direkte adgang findes kun for PostgreSQL")
    try:
        return await psycopg.AsyncConnection.connect(
            host=definition["host"], port=definition["port"], dbname=definition["database"], user=definition["user"],
            password=password or None, sslmode=definition.get("sslmode") or "prefer", connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=5000", row_factory=dict_row, autocommit=False)
    except psycopg.Error:
        raise ConnectionError_(f"Kunne ikke forbinde til {definition['host']}:{definition['port']}/{definition['database']}")


async def describe_direct(definition: dict, password: str | None) -> dict[str, list[dict]]:
    async with await direct(definition, password) as rc:
        rows = await db.fetch_all(rc, """select table_name, column_name, data_type from information_schema.columns
                                         where table_schema = %s order by table_name, ordinal_position""", (definition["schema"],))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["table_name"], []).append({"name": r["column_name"], "data_type": r["data_type"]})
    return out


# ── FDW ─────────────────────────────────────────────────────────────────────

def _admin_url() -> str:
    s = config.settings
    url = s.dev_fdw_admin_url or (s.migration_database_url if not s.analytics_database_url else None)
    if not url:
        raise ConnectionError_("DEV_FDW_ADMIN_URL er ikke sat: der er ingen rolle, der må oprette fremmede servere", 409)
    return url


async def _runtime_roles() -> list[str]:
    roles = []
    async with db.analytics_connection() as conn:
        roles.append((await db.fetch_one(conn, "select current_user as u"))["u"])
    async with db.pipeline_connection() as conn:
        roles.append((await db.fetch_one(conn, "select current_user as u"))["u"])
    return sorted(set(roles))


async def apply_fdw(source: dict, definition: dict, password: str | None) -> list[str]:
    """Creates or updates the foreign server, user mappings and the foreign tables. Returns the tables imported."""
    problem = await fdw_problem(definition["type"])
    if problem:
        raise ConnectionError_(problem, 409)
    kind = definition["type"]
    server, schema = server_name(source["key"]), schema_name(source["key"])
    if kind == "postgres":
        options = {"host": definition["host"], "port": str(definition["port"]), "dbname": definition["database"]}
        mapping = {"user": definition["user"], "password": password or ""}
    else:
        options = {"host": definition["host"], "port": str(definition["port"])}
        mapping = {"username": definition["user"], "password": password or ""}
    roles = await _runtime_roles()
    try:
        async with await psycopg.AsyncConnection.connect(_admin_url(), autocommit=False) as ac:
            me = (await (await ac.execute("select current_user")).fetchone())[0]
            mapped = sorted(set(roles) | {me})    # IMPORT FOREIGN SCHEMA connects as the admin role itself
            exists = await (await ac.execute("select 1 from pg_foreign_server where srvname = %s", (server,))).fetchone()
            opts = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(k), sql.Literal(v)) for k, v in options.items())
            if not exists:
                await ac.execute(sql.SQL("create server {} foreign data wrapper {} options ({})").format(
                    sql.Identifier(server), sql.Identifier(FDW[kind]), opts))
            else:
                sets = sql.SQL(", ").join(sql.SQL("set {} {}").format(sql.Identifier(k), sql.Literal(v)) for k, v in options.items())
                await ac.execute(sql.SQL("alter server {} options ({})").format(sql.Identifier(server), sets))
            for role in mapped:
                await ac.execute(sql.SQL("drop user mapping if exists for {} server {}").format(sql.Identifier(role), sql.Identifier(server)))
                m = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(k), sql.Literal(v)) for k, v in mapping.items() if v != "" or k != "password")
                await ac.execute(sql.SQL("create user mapping for {} server {} options ({})").format(
                    sql.Identifier(role), sql.Identifier(server), m))
            await ac.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
            await ac.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
            if definition["tables"]:
                await ac.execute(sql.SQL("import foreign schema {} limit to ({}) from server {} into {}").format(
                    sql.Identifier(definition["schema"]), sql.SQL(", ").join(sql.Identifier(t) for t in definition["tables"]),
                    sql.Identifier(server), sql.Identifier(schema)))
            for role in roles:
                await ac.execute(sql.SQL("grant usage on schema {} to {}").format(sql.Identifier(schema), sql.Identifier(role)))
                await ac.execute(sql.SQL("grant select on all tables in schema {} to {}").format(sql.Identifier(schema), sql.Identifier(role)))
            await ac.commit()
    except psycopg.Error as e:
        raise ConnectionError_(f"FDW kunne ikke sættes op: {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}", 424)
    async with db.analytics_connection() as conn:
        rows = await db.fetch_all(conn, "select table_name from information_schema.tables where table_schema = %s", (schema,))
    return sorted(r["table_name"] for r in rows)


async def drop_fdw(key: str) -> None:
    try:
        async with await psycopg.AsyncConnection.connect(_admin_url(), autocommit=True) as ac:
            await ac.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema_name(key))))
            await ac.execute(sql.SQL("drop server if exists {} cascade").format(sql.Identifier(server_name(key))))
    except (psycopg.Error, HTTPException):
        pass


async def describe_fdw(key: str) -> dict[str, list[dict]]:
    async with db.analytics_connection() as conn:
        rows = await db.fetch_all(conn, """select table_name, column_name, data_type from information_schema.columns
                                           where table_schema = %s order by table_name, ordinal_position""", (schema_name(key),))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["table_name"], []).append({"name": r["column_name"], "data_type": r["data_type"]})
    return out


# ── The catalog ─────────────────────────────────────────────────────────────

SECRETISH = re.compile(r"(password|passwd|secret|token|api_?key|private_key|salt|hash)", re.I)


async def import_catalog(conn, source: dict, definition: dict, columns: dict[str, list[dict]]) -> list[int]:
    """One dataset (kind foreign) per table the connection offers; new fields start yellow until classified (F-7).
    Columns that look like secrets are left out. Tables no longer offered leave the catalog."""
    ids = []
    for table in definition.get("tables") or []:
        cols = columns.get(table)
        if cols is None:
            continue
        d = await db.fetch_one(conn, "select id from datasets where source_id = %s and key = %s", (source["id"], table))
        if d is None:
            d = await db.fetch_one(conn, """insert into datasets (source_id, key, name, kind, refreshed_at)
                                            values (%s, %s, %s, 'foreign', now()) returning id""", (source["id"], table, table))
        have = {r["name"] for r in await db.fetch_all(conn, "select name from dataset_fields where dataset_id = %s", (d["id"],))}
        for pos, c in enumerate(cols):
            if c["name"] in have or SECRETISH.search(c["name"]):
                continue
            await conn.execute("""insert into dataset_fields (dataset_id, name, data_type, position) values (%s, %s, %s, %s)""",
                               (d["id"], c["name"], c["data_type"], pos))
        await conn.execute("update datasets set refreshed_at = now(), updated_at = now(), kind = 'foreign' where id = %s", (d["id"],))
        ids.append(d["id"])
    await conn.execute("delete from datasets where source_id = %s and kind = 'foreign' and not (key = any(%s))",
                       (source["id"], list(definition.get("tables") or [])))
    return ids


def table_ref(dataset: dict, source: dict | None) -> tuple[str, str]:
    """(schema, table) of a catalog dataset in the analytics database."""
    if dataset["kind"] == "snapshot":
        schema, _, table = dataset["key"].partition(".")
        return schema, table
    if dataset["kind"] == "foreign" and source is not None:
        return schema_name(source["key"]), dataset["key"]
    if dataset["kind"] == "table" and dataset.get("source_id") is None:
        return "public", dataset["key"]
    raise HTTPException(422, f"{dataset['name']} er ikke en tabel i databasen (API-kilder bruges via multisource)")
