"""/admin → Data: the catalog, Udforsk and API sources (specs/admin-app/DATA.md).

- A dataset is a table in this database (added from the list of tables) or an endpoint of an API source
  (created from its kildebeskrivelse, app/data_sources.py). The catalog holds Danish names, descriptions and
  a classification per field: white, green, yellow, red.
- Udforsk reads only. Tables are read in a READ ONLY transaction with a 5 s timeout; tables with codes, keys
  or sessions are never offered, and columns that look like secrets are left out of the catalog.
- Yellow and red fields are masked. An admin unmasks a dataset for 15 minutes by giving a reason; that is an
  audit_log entry (data.unmask), and the entry is also what grants it. Masked fields cannot be filtered or sorted.
- Every change to the catalog is audited in the same transaction.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg import sql
from pydantic import BaseModel, Field

from app import data_sources as sources_mod
from app import db, integrations, secret_store, source_secrets
from app.data_sources import FetchError, SpecError
from app.logs import audit, changed
from app.mcp.db_tools import SECRET_COLUMN
from app.sessions import Principal, require_admin

router = APIRouter(prefix="/api/admin/data", tags=["admin"], dependencies=[Depends(require_admin)])

CLASSES = "^(white|green|yellow|red)$"
MASKED = {"yellow", "red"}
MASK = "•••"
UNMASK_MINUTES = 15
OWN_SOURCE = {"id": None, "key": "gaer", "name": "Tørgær (egen database)", "kind": "gaer", "hosts": []}

# Never offered in Udforsk: one-time codes, challenges, sessions, tokens, authenticator secrets, the auth log
# (phone/e-mail hashes), settings, the migration list and the catalog itself.
HIDDEN_TABLES = {"otp_codes", "webauthn_challenges", "sessions", "mcp_tokens", "mcp_clients", "totp_factors", "auth_log",
                 "settings", "schema_migrations", "data_sources", "datasets", "dataset_fields",
                 "mcp_access_links", "mcp_tools", "mcp_tool_endpoints",
                 # Hemmeligheder: values, who may approve and who has access are never explored from here.
                 "secrets", "secret_versions", "secret_raci", "secret_access_requests", "secret_approvals", "secret_grants",
                 "secret_notifications", "group_notify_rules", "data_source_secrets"}
TABLE_NAMES = {"users": "Brugere", "clips": "Klip", "files": "Filer", "audit_log": "Audit-log", "web_pages": "Website-sider",
               "features": "Features", "feature_rings": "Feature-niveauer", "feature_usage": "Feature-brug",
               "consents": "Samtykker", "bridge_consents": "Samtykker til udvidelsen", "gdpr_requests": "GDPR-forespørgsler",
               "analytics_events": "Analytics-hændelser", "ai_usage": "AI-forbrug", "ai_budgets": "AI-budgetter",
               "ai_budget_alerts": "AI-budgetadvarsler", "ai_model_prices": "AI-priser pr. model", "app_store": "App Stores",
               "passkeys": "Passkeys", "mcp_drafts": "MCP-kladder", "mcp_log": "MCP-log"}
# Column types that are compared as their own type (> 10 on a number); everything else is compared as text.
COMPARABLE = {"smallint": "smallint", "integer": "integer", "bigint": "bigint", "numeric": "numeric", "real": "real",
              "double precision": "double precision", "date": "date",
              "timestamp with time zone": "timestamptz", "timestamp without time zone": "timestamp"}
OPS = "^(eq|neq|contains|not_contains|starts_with|in|gt|gte|lt|lte|empty|not_empty)$"


# ── Catalog ─────────────────────────────────────────────────────────────────

@router.get("/catalog")
async def catalog():
    async with db.connection() as conn:
        sources = await db.fetch_all(conn, """
            select s.id, s.key, s.name, s.kind, s.hosts, s.updated_at,
                   (select count(*) from datasets d where d.source_id = s.id) as datasets
            from data_sources s order by s.name""")
        rows = await db.fetch_all(conn, """
            select d.id, d.source_id, d.key, d.name, d.description, d.classification, d.personal_data, d.mcp_exposed,
                   d.version, d.updated_at, coalesce(s.name, %s) as source_name, coalesce(s.kind, 'gaer') as source_kind,
                   (select count(*) from dataset_fields f where f.dataset_id = d.id) as fields,
                   (select count(*) from dataset_fields f where f.dataset_id = d.id and f.description <> '') as described,
                   case when d.source_id is null then
                     (select greatest(c.reltuples, 0)::bigint from pg_class c
                       where c.relname = d.key and c.relnamespace = 'public'::regnamespace) end as approx_rows
            from datasets d left join data_sources s on s.id = d.source_id
            order by d.source_id nulls first, d.name""", (OWN_SOURCE["name"],))
        unmasks = await db.fetch_one(conn, """
            select count(*) as n from audit_log where action = 'data.unmask' and ts > now() - interval '30 days'""")
    own = sum(1 for r in rows if r["source_id"] is None)
    fields = sum(r["fields"] for r in rows)
    described = sum(r["described"] for r in rows)
    return {"sources": [{**OWN_SOURCE, "datasets": own}] + sources, "datasets": rows,
            "stats": {"datasets": len(rows), "sources": len(sources) + 1, "fields": fields,
                      "described_pct": round(100 * described / fields) if fields else 0,
                      "mcp": sum(1 for r in rows if r["mcp_exposed"]), "unmasks_30d": unmasks["n"]}}


@router.get("/tables")
async def tables():
    """Tables in this database that are not in the catalog yet (hidden tables are never listed)."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select c.relname as name, greatest(c.reltuples, 0)::bigint as approx_rows,
                   (select count(*) from information_schema.columns ic
                     where ic.table_schema = 'public' and ic.table_name = c.relname) as columns
            from pg_class c
            where c.relkind in ('r', 'p') and c.relnamespace = 'public'::regnamespace and not c.relispartition
              and c.relname not in (select key from datasets where source_id is null)
            order by c.relname""")
    return [{**r, "label": TABLE_NAMES.get(r["name"], "")} for r in rows if r["name"] not in HIDDEN_TABLES]


class TableIn(BaseModel):
    table: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")


@router.post("/datasets", status_code=201)
async def add_table(body: TableIn, admin: Principal = Depends(require_admin)):
    """Adds a table of this database to the catalog. New fields start as yellow (masked) until classified."""
    if body.table in HIDDEN_TABLES:
        raise HTTPException(422, "Tabellen indeholder koder, nøgler eller sessioner og kan ikke udforskes")
    async with db.connection() as conn:
        table = await db.fetch_one(conn, """
            select relname from pg_class where relname = %s and relkind in ('r', 'p')
              and relnamespace = 'public'::regnamespace and not relispartition""", (body.table,))
        if not table:
            raise HTTPException(404, "Tabellen findes ikke")
        if await db.fetch_one(conn, "select 1 from datasets where source_id is null and key = %s", (body.table,)):
            raise HTTPException(409, "Tabellen er allerede i kataloget")
        cols = await db.fetch_all(conn, """
            select column_name, data_type from information_schema.columns
            where table_schema = 'public' and table_name = %s order by ordinal_position""", (body.table,))
        names = {c["column_name"] for c in cols}
        personal = body.table == "users" or "user_id" in names
        row = await db.fetch_one(conn, """
            insert into datasets (key, name, personal_data) values (%s, %s, %s) returning id""",
            (body.table, TABLE_NAMES.get(body.table) or body.table, personal))
        omitted = []
        for pos, c in enumerate(cols):
            if SECRET_COLUMN.match(c["column_name"]):
                omitted.append(c["column_name"])
                continue
            await conn.execute("""
                insert into dataset_fields (dataset_id, name, data_type, position) values (%s, %s, %s, %s)""",
                (row["id"], c["column_name"], c["data_type"], pos))
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="datasets", row_id=row["id"],
                    after={"key": body.table, "source": "gaer"}, omitted_columns=omitted)
        return await _detail(conn, row["id"], admin)


# ── One dataset ─────────────────────────────────────────────────────────────

async def _dataset(conn, dataset_id: int, lock: bool = False) -> dict:
    d = await db.fetch_one(conn, f"""
        select d.*, s.key as source_key, s.name as source_name, s.spec_yaml
        from datasets d left join data_sources s on s.id = d.source_id where d.id = %s
        {"for update of d" if lock else ""}""", (dataset_id,))
    if not d:
        raise HTTPException(404, "Datasættet findes ikke")
    return d


async def _fields(conn, dataset_id: int) -> list[dict]:
    return await db.fetch_all(conn, """
        select id, name, path, label, description, data_type, classification, position
        from dataset_fields where dataset_id = %s order by position, id""", (dataset_id,))


async def _unmasked_until(conn, dataset_id: int, admin: Principal) -> datetime | None:
    row = await db.fetch_one(conn, """
        select max(ts) as ts from audit_log
        where action = 'data.unmask' and table_name = 'datasets' and row_id = %s and actor_user_id = %s""",
        (str(dataset_id), admin.impersonator_id or admin.user_id))
    if not row or not row["ts"]:
        return None
    until = row["ts"] + timedelta(minutes=UNMASK_MINUTES)
    return until if until > datetime.now(timezone.utc) else None


async def _detail(conn, dataset_id: int, admin: Principal) -> dict:
    d = await _dataset(conn, dataset_id)
    call = None
    if d["source_id"] is not None:
        try:
            spec = sources_mod.dataset_spec(sources_mod.parse_spec(d["spec_yaml"]), d["key"])
            call = {"method": spec["method"], "path": spec["path"], "params": spec["params"]}
        except SpecError:
            call = None
    d.pop("spec_yaml", None)
    return {**d, "source_key": d["source_key"] or "gaer", "source_name": d["source_name"] or OWN_SOURCE["name"],
            "source_kind": "rest" if d["source_id"] else "gaer", "call": call,
            "fields": await _fields(conn, dataset_id), "unmasked_until": await _unmasked_until(conn, dataset_id, admin)}


@router.get("/datasets/{dataset_id}")
async def dataset(dataset_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        return await _detail(conn, dataset_id, admin)


class DatasetIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    owner: str | None = Field(default=None, max_length=120)
    classification: str | None = Field(default=None, pattern=CLASSES)
    personal_data: bool | None = None
    mcp_exposed: bool | None = None


@router.patch("/datasets/{dataset_id}")
async def update_dataset(dataset_id: int, body: DatasetIn, admin: Principal = Depends(require_admin)):
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "Intet at ændre")
    async with db.connection() as conn:
        d = await _dataset(conn, dataset_id, lock=True)
        before, after = changed({k: d[k] for k in values}, values)
        if after:
            sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in after)
            await conn.execute(sql.SQL("update datasets set {}, updated_at = now() where id = %s").format(sets),
                               [*after.values(), dataset_id])
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="datasets", row_id=dataset_id,
                        before=before, after=after)
        return await _detail(conn, dataset_id, admin)


class FieldIn(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    classification: str | None = Field(default=None, pattern=CLASSES)


@router.patch("/datasets/{dataset_id}/fields/{field_id}")
async def update_field(dataset_id: int, field_id: int, body: FieldIn, admin: Principal = Depends(require_admin)):
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "Intet at ændre")
    async with db.connection() as conn:
        f = await db.fetch_one(conn, "select * from dataset_fields where id = %s and dataset_id = %s for update",
                               (field_id, dataset_id))
        if not f:
            raise HTTPException(404, "Feltet findes ikke")
        before, after = changed({k: f[k] for k in values}, values)
        if after:
            sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in after)
            await conn.execute(sql.SQL("update dataset_fields set {} where id = %s").format(sets), [*after.values(), field_id])
            await conn.execute("update datasets set updated_at = now() where id = %s", (dataset_id,))
            await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="dataset_fields", row_id=field_id,
                        before=before, after=after, dataset_id=dataset_id, field=f["name"])
        return {**f, **after}


@router.delete("/datasets/{dataset_id}")
async def remove_dataset(dataset_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        d = await _dataset(conn, dataset_id, lock=True)
        if d["source_id"] is not None:
            raise HTTPException(409, "Datasæt fra en API-kilde fjernes ved at ændre kildebeskrivelsen")
        await conn.execute("delete from datasets where id = %s", (dataset_id,))
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="datasets", row_id=dataset_id,
                    before={"key": d["key"], "name": d["name"]})
    return {"ok": True}


class UnmaskIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.post("/datasets/{dataset_id}/unmask")
async def unmask(dataset_id: int, body: UnmaskIn, admin: Principal = Depends(require_admin)):
    """Shows the yellow and red fields of one dataset to this admin for 15 minutes. The audit entry is the grant."""
    async with db.connection() as conn:
        d = await _dataset(conn, dataset_id)
        await audit(conn, "data.unmask", actor_id=admin.user_id, meta=admin.meta, table="datasets", row_id=dataset_id,
                    reason=body.reason.strip(), dataset=d["key"], minutes=UNMASK_MINUTES)
    return {"unmasked_until": datetime.now(timezone.utc) + timedelta(minutes=UNMASK_MINUTES)}


# ── Udforsk ─────────────────────────────────────────────────────────────────

class FilterIn(BaseModel):
    field: str = Field(max_length=63)
    op: str = Field(pattern=OPS)
    value: str = Field(default="", max_length=500)


class QueryIn(BaseModel):
    filters: list[FilterIn] = Field(default_factory=list, max_length=10)
    sort: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)


def _plain(value):
    if isinstance(value, (bytes, memoryview)):
        return "<binær>"
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


@router.post("/datasets/{dataset_id}/query")
async def query(dataset_id: int, body: QueryIn, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        d = await _dataset(conn, dataset_id)
        fields = [f for f in await _fields(conn, dataset_id) if not (d["source_id"] is None and SECRET_COLUMN.match(f["name"]))]
        until = await _unmasked_until(conn, dataset_id, admin)
    by_name = {f["name"]: f for f in fields}
    masked = {f["name"] for f in fields if f["classification"] in MASKED} if until is None else set()

    sort_field, desc = None, False
    if body.sort:
        sort_field, desc = body.sort.lstrip("-"), body.sort.startswith("-")
    for name in [flt.field for flt in body.filters] + ([sort_field] if sort_field else []):
        if name not in by_name:
            raise HTTPException(422, f"Feltet {name} findes ikke i datasættet")
        if name in masked:
            raise HTTPException(422, f"{by_name[name]['label'] or name} er maskeret. Vis maskerede felter med en årsag "
                                     "for at filtrere eller sortere på det")

    if d.get("kind") in ("foreign", "snapshot"):
        # Gær Developer: foreign tables (fdw_<forbindelse>) and snapshots live in the analytics database.
        from app.devkit.connections import table_ref
        schema, table = table_ref(d, {"key": d["source_key"]} if d["source_id"] else None)
        result = await _query_table(table, fields, body, sort_field, desc, schema=schema)
    elif d["source_id"] is None:
        result = await _query_table(d["key"], fields, body, sort_field, desc)
    else:
        result = await _query_api(d, fields, body, sort_field, desc, admin)

    rows = [{k: (MASK if k in masked and v is not None else _plain(v)) for k, v in r.items()} for r in result["rows"]]
    columns = [{"name": f["name"], "label": f["label"] or f["name"], "data_type": f["data_type"],
                "classification": f["classification"], "masked": f["name"] in masked} for f in fields]
    return {**result, "rows": rows, "columns": columns, "unmasked_until": until}


async def _query_table(table: str, fields: list[dict], body: QueryIn, sort_field: str | None, desc: bool,
                       schema: str = "public") -> dict:
    by_name = {f["name"]: f for f in fields}
    conditions, params = [], []
    for flt in body.filters:
        col = sql.Identifier(flt.field)
        as_text = sql.SQL("{}::text").format(col)
        cast = COMPARABLE.get(by_name[flt.field]["data_type"])
        typed = sql.SQL("cast(%s as {})").format(sql.SQL(cast)) if cast else sql.SQL("%s")
        left = col if cast else as_text
        if flt.op in ("empty", "not_empty"):
            cond = sql.SQL("({} is null or {} = '')").format(col, as_text)
            conditions.append(cond if flt.op == "empty" else sql.SQL("not {}").format(cond))
            continue
        if flt.op in ("contains", "not_contains", "starts_with"):
            pattern = flt.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"{pattern}%" if flt.op == "starts_with" else f"%{pattern}%"
            cond = sql.SQL("{} ilike %s").format(as_text)
            conditions.append(sql.SQL("({} is not null and not {})").format(col, cond) if flt.op == "not_contains" else cond)
            params.append(pattern)
            continue
        if flt.op == "in":
            values = [v.strip() for v in flt.value.split(",") if v.strip()][:50]
            conditions.append(sql.SQL("{} = any(%s)").format(as_text))
            params.append(values)
            continue
        symbol = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[flt.op]
        conditions.append(sql.SQL("{} {} {}").format(left, sql.SQL(symbol), typed))
        params.append(flt.value)

    where = sql.SQL(" where ") + sql.SQL(" and ").join(conditions) if conditions else sql.SQL("")
    if sort_field:
        order = sql.SQL(" order by {} {} nulls last").format(sql.Identifier(sort_field), sql.SQL("desc" if desc else "asc"))
    elif "id" in by_name:
        order = sql.SQL(" order by id desc")
    else:
        order = sql.SQL("")
    columns = sql.SQL(", ").join(sql.Identifier(f["name"]) for f in fields)
    ident = sql.Identifier(table) if schema == "public" else sql.Identifier(schema, table)
    select = sql.SQL("select {} from {}").format(columns, ident) + where + order + sql.SQL(" limit %s offset %s")
    count = sql.SQL("select count(*) as n from {}").format(ident) + where
    try:
        async with (db.connection() if schema == "public" else db.analytics_connection()) as conn:
            await conn.execute("set transaction read only")
            await conn.execute("set local statement_timeout = '5s'")
            rows = await db.fetch_all(conn, select, [*params, body.limit, body.offset])
            total = (await db.fetch_one(conn, count, params))["n"]
    except psycopg.errors.QueryCanceled:
        raise HTTPException(422, "Forespørgslen tog mere end 5 sekunder og blev stoppet. Brug et filter mere")
    except psycopg.errors.UndefinedColumn:
        raise HTTPException(409, "Tabellen har ændret sig siden den kom i kataloget. Fjern den og tilføj den igen")
    except (psycopg.errors.DataError, psycopg.errors.InvalidTextRepresentation, psycopg.errors.InvalidDatetimeFormat):
        raise HTTPException(422, "En filterværdi passer ikke til feltets type")
    return {"rows": rows, "total": total, "has_more": body.offset + len(rows) < total, "filters": [], "source": None}


def _local_match(value, op: str, target: str) -> bool:
    if op == "empty":
        return value in (None, "")
    if op == "not_empty":
        return value not in (None, "")
    text = "" if value is None else str(value)
    if op == "eq":
        return text == target
    if op == "neq":
        return text != target
    if op == "contains":
        return target.lower() in text.lower()
    if op == "not_contains":
        return value is not None and target.lower() not in text.lower()
    if op == "starts_with":
        return text.lower().startswith(target.lower())
    if op == "in":
        return text in {v.strip() for v in target.split(",")}
    try:
        a, b = float(text), float(target)
    except ValueError:
        a, b = text, target
    if value is None:
        return False
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


async def _query_api(d: dict, fields: list[dict], body: QueryIn, sort_field: str | None, desc: bool,
                     admin: Principal) -> dict:
    try:
        spec = sources_mod.parse_spec(d["spec_yaml"])
        ds = sources_mod.dataset_spec(spec, d["key"])
    except SpecError as e:
        raise HTTPException(409, f"Kildebeskrivelsen kan ikke bruges: {e}")
    async with db.connection() as conn:
        secrets, _ = await source_secrets.resolve(conn, spec, source_id=d["source_id"], meta=admin.meta)
    api_params, local, how = {}, [], []
    for flt in body.filters:
        if flt.field in ds["params"] and flt.op == "eq" and flt.field not in api_params:
            api_params[flt.field] = flt.value
            how.append({"field": flt.field, "where": "api"})
        else:
            local.append(flt)
            how.append({"field": flt.field, "where": "lokalt"})
    paged = ds["paging"] is not None
    try:
        result = await sources_mod.fetch(spec, ds, api_params, page=body.offset // body.limit + 1 if paged else None,
                                         size=body.limit, secrets=secrets)
    except FetchError as e:
        raise HTTPException(424, e.message)
    rows = [r for r in result["rows"] if all(_local_match(r.get(f.field), f.op, f.value) for f in local)]
    if sort_field:
        rows.sort(key=lambda r: (r.get(sort_field) is None, str(r.get(sort_field) or "")), reverse=desc)
    if paged:
        total, has_more = None, len(result["rows"]) >= body.limit
    else:
        total = len(rows)
        rows = rows[body.offset:body.offset + body.limit]
        has_more = body.offset + len(rows) < total
    names = [f["name"] for f in fields]
    return {"rows": [{n: r.get(n) for n in names} for r in rows], "total": total, "has_more": has_more, "filters": how,
            "source": {"url": result["url"], "ms": result["ms"], "cached": result["cached"]}}


# ── API sources ─────────────────────────────────────────────────────────────

class SpecIn(BaseModel):
    spec_yaml: str = Field(min_length=1, max_length=64_000)


class TestIn(SpecIn):
    dataset: str | None = Field(default=None, max_length=40)
    source_id: int | None = None   # editing this source: its own key is not a conflict


def _parse(text: str) -> dict:
    try:
        return sources_mod.parse_spec(text)
    except SpecError as e:
        raise HTTPException(422, str(e))


@router.post("/sources/parse")
async def parse_source(body: SpecIn):
    """The description as the server reads it (for the form in Forbind API). Names of secrets only, never values."""
    return _parse(body.spec_yaml)


@router.post("/sources/test")
async def test_source(body: TestIn, admin: Principal = Depends(require_admin)):
    """Validates a kildebeskrivelse and calls one dataset. Changes nothing but the audit log (a secret used through
    the tester's own access is audited on the secret)."""
    spec = _parse(body.spec_yaml)
    checks = [{"ok": True, "text": f"{'Værterne' if len(spec['hosts']) > 1 else 'Værten'} {', '.join(spec['hosts'])} "
                                   "kommer på listen over tilladte værter"},
              {"ok": True, "text": "Ingen nøgler i beskrivelsen — kun navne på hemmeligheder"}]
    async with db.connection() as conn:
        tester = await secret_store.user_row(conn, admin.user_id)
        secrets, secret_status = await source_secrets.resolve(conn, spec, source_id=body.source_id, tester=tester,
                                                              meta=admin.meta)
        checks += [{"ok": st["ok"], "text": st["text"]} for st in secret_status]
        clash = await db.fetch_one(conn, "select id from data_sources where key = %s", (spec["key"],))
    if clash and clash["id"] != body.source_id:
        checks.append({"ok": False, "text": f"Der findes allerede en kilde med navnet {spec['key']}"})
    for ds in spec["datasets"]:
        unnamed = [f["name"] for f in ds["fields"] if not f["label"]]
        if unnamed:
            checks.append({"ok": False, "text": f"{ds['name']}: {', '.join(unnamed[:5])} har intet dansk navn"})
        masked = [f["name"] for f in ds["fields"] if f["classification"] in MASKED]
        if masked:
            checks.append({"ok": False, "text": f"{ds['name']}: {len(masked)} felter er gule eller røde og vises maskeret"})

    ds = sources_mod.dataset_spec(spec, body.dataset) if body.dataset else spec["datasets"][0]
    preview, error = None, None
    try:
        result = await sources_mod.fetch(spec, ds, {}, page=1 if ds["paging"] else None, size=5, secrets=secrets)
        rows = [{k: (MASK if v is not None and f["classification"] in MASKED else _plain(v))
                 for f in ds["fields"] for k, v in [(f["name"], r.get(f["name"]))]} for r in result["rows"][:5]]
        preview = {"dataset": ds["key"], "url": result["url"], "ms": result["ms"], "rows": rows,
                   "columns": [{"name": f["name"], "label": f["label"] or f["name"], "masked": f["classification"] in MASKED}
                               for f in ds["fields"]]}
        checks.append({"ok": True, "text": f"Testkald til {ds['name']} gav {len(result['rows'])} rækker"})
    except FetchError as e:
        error = e.message
        checks.append({"ok": False, "text": f"Testkald til {ds['name']}: {e.message}"})
    return {"source": {"key": spec["key"], "name": spec["name"], "host": spec["host"], "hosts": spec["hosts"],
                       "auth": spec["auth"]["type"]},
            "secrets": [_secret_status_out(st) for st in secret_status],
            "datasets": [{"key": d["key"], "name": d["name"], "call": f"{d['method']} {d['path']}", "fields": len(d["fields"]),
                          "paged": d["paging"] is not None} for d in spec["datasets"]],
            "preview": preview, "error": error, "checks": checks}


async def _sync(conn, source_id: int, spec: dict) -> dict:
    """Makes the source's datasets and fields match the description. Admin edits of names, descriptions and
    classifications are kept for fields that stay; new fields take the values from the description."""
    existing = {r["key"]: r for r in await db.fetch_all(conn, "select id, key from datasets where source_id = %s",
                                                         (source_id,))}
    added, removed = [], [k for k in existing if k not in {d["key"] for d in spec["datasets"]}]
    for key in removed:
        await conn.execute("delete from datasets where id = %s", (existing[key]["id"],))
    for ds in spec["datasets"]:
        if ds["key"] in existing:
            dataset_id = existing[ds["key"]]["id"]
            await conn.execute("update datasets set name = %s, updated_at = now() where id = %s", (ds["name"], dataset_id))
        else:
            row = await db.fetch_one(conn, """
                insert into datasets (source_id, key, name, description, classification) values (%s, %s, %s, %s, %s)
                returning id""", (source_id, ds["key"], ds["name"], ds["description"], ds["classification"]))
            dataset_id = row["id"]
            added.append(ds["key"])
        names = [f["name"] for f in ds["fields"]]
        await conn.execute("delete from dataset_fields where dataset_id = %s and not (name = any(%s))", (dataset_id, names))
        for f in ds["fields"]:
            await conn.execute("""
                insert into dataset_fields (dataset_id, name, path, label, description, data_type, classification, position)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (dataset_id, name) do update set path = excluded.path, data_type = excluded.data_type,
                    position = excluded.position,
                    label = case when excluded.label <> '' then excluded.label else dataset_fields.label end""",
                (dataset_id, f["name"], f["path"], f["label"], f["description"], f["data_type"], f["classification"],
                 f["position"]))
    await source_secrets.sync(conn, source_id, spec)
    return {"added": added, "removed": removed}


@router.get("/sources/{source_id}")
async def source(source_id: int):
    async with db.connection() as conn:
        s = await db.fetch_one(conn, "select * from data_sources where id = %s", (source_id,))
        if not s:
            raise HTTPException(404, "Kilden findes ikke")
        s["datasets"] = await db.fetch_all(conn, "select id, key, name from datasets where source_id = %s order by name",
                                           (source_id,))
        try:
            spec = sources_mod.parse_spec(s["spec_yaml"])
        except SpecError:
            spec = {"key": s["key"], "secrets": []}
        _, status = await source_secrets.resolve(conn, spec, source_id=source_id, values_wanted=False)
        s["secrets"] = [_secret_status_out(st) for st in status]
    return s


@router.post("/sources", status_code=201)
async def create_source(body: SpecIn, admin: Principal = Depends(require_admin)):
    spec = _parse(body.spec_yaml)
    async with db.connection() as conn:
        if await db.fetch_one(conn, "select 1 from data_sources where key = %s", (spec["key"],)):
            raise HTTPException(409, f"Der findes allerede en kilde med navnet {spec['key']}")
        row = await db.fetch_one(conn, """
            insert into data_sources (key, name, kind, spec_yaml, hosts) values (%s, %s, %s, %s, %s) returning id""",
            (spec["key"], spec["name"], spec["kind"], body.spec_yaml, spec["hosts"]))
        result = await _sync(conn, row["id"], spec)
        await audit(conn, "create", actor_id=admin.user_id, meta=admin.meta, table="data_sources", row_id=row["id"],
                    after={"key": spec["key"], "name": spec["name"], "hosts": spec["hosts"], "auth": spec["auth"]["type"],
                           "secrets": [x["ref"] for x in spec["secrets"]]}, datasets=result["added"])
    return {"id": row["id"], **result}


@router.put("/sources/{source_id}")
async def update_source(source_id: int, body: SpecIn, admin: Principal = Depends(require_admin)):
    spec = _parse(body.spec_yaml)
    async with db.connection() as conn:
        s = await db.fetch_one(conn, "select * from data_sources where id = %s for update", (source_id,))
        if not s:
            raise HTTPException(404, "Kilden findes ikke")
        if spec["key"] != s["key"]:
            raise HTTPException(422, f"kilde kan ikke ændres. Den hedder {s['key']}")
        await conn.execute("""
            update data_sources set name = %s, spec_yaml = %s, hosts = %s, updated_at = now() where id = %s""",
            (spec["name"], body.spec_yaml, spec["hosts"], source_id))
        old_refs = [x["ref"] for x in _refs(s["spec_yaml"])]
        result = await _sync(conn, source_id, spec)
        before, after = changed({"name": s["name"], "hosts": s["hosts"], "spec_bytes": len(s["spec_yaml"]), "secrets": old_refs},
                                {"name": spec["name"], "hosts": spec["hosts"], "spec_bytes": len(body.spec_yaml),
                                 "secrets": [x["ref"] for x in spec["secrets"]]})
        await audit(conn, "update", actor_id=admin.user_id, meta=admin.meta, table="data_sources", row_id=source_id,
                    before=before, after=after, **result)
    sources_mod.clear_cache()
    return {"id": source_id, **result}


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, admin: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        s = await db.fetch_one(conn, "select * from data_sources where id = %s for update", (source_id,))
        if not s:
            raise HTTPException(404, "Kilden findes ikke")
        keys = [r["key"] for r in await db.fetch_all(conn, "select key from datasets where source_id = %s", (source_id,))]
        await conn.execute("delete from data_sources where id = %s", (source_id,))
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="data_sources", row_id=source_id,
                    before={"key": s["key"], "name": s["name"], "hosts": s["hosts"]}, datasets=keys)
    sources_mod.clear_cache()
    return {"ok": True}


def _refs(spec_yaml: str) -> list[dict]:
    try:
        return sources_mod.parse_spec(spec_yaml)["secrets"]
    except SpecError:
        return []


def _secret_status_out(st: dict) -> dict:
    out = {k: st.get(k) for k in ("ref", "used_as", "kind", "ok", "how", "text", "secret_id", "grant_expires_at")}
    p = st.get("pending")
    out["pending"] = {"id": p["id"], "approved": p["approved"], "total": p["total"], "expires_at": p["expires_at"]} if p else None
    return out


# ── Secrets for sources (Hemmeligheder) ─────────────────────────────────────

@router.get("/secrets")
async def secrets_for_sources(source_id: int | None = None, admin: Principal = Depends(require_admin)):
    """The secrets a source can name, for the picker in Forbind API: names, owner, RACI rule and approvers, the
    source's access and the admin's own. Never a value or a hint of one."""
    async with db.connection() as conn:
        me = await secret_store.user_row(conn, admin.user_id)
        rows = await db.fetch_all(conn, f"""
            select {secret_store.SECRET_COLUMNS}, g.slug as group_slug from secrets s join groups g on g.id = s.group_id
            where s.archived_at is null order by lower(g.name), lower(s.name), s.environment""")
        out = []
        for sec in rows:
            raci = await secret_store.raci_map(conn, sec["id"])
            users = await secret_store.users_by_id(conn, raci)
            a_user = next((users.get(u) for u, rs in raci.items() if "A" in rs), None)
            approvers = secret_store.approvers_for(sec["approval_rule"], raci, {admin.user_id}, users)
            grant = await source_secrets.source_grant(conn, sec["id"], source_id)
            pending = await source_secrets.pending_request(conn, sec["id"], source_id)
            out.append({
                "id": sec["id"], "ref": source_secrets.ref_of(sec), "name": sec["name"], "environment": sec["environment"],
                "description": sec["description"], "group": {"id": sec["group_id"], "name": sec["group_name"],
                                                              "slug": sec["group_slug"]},
                "version": sec["current_version"], "value_changed_at": sec["value_changed_at"],
                "rotation_days": sec["rotation_days"], "expires_at": sec["expires_at"],
                "status": secret_store.status_of(sec, has_a=bool(a_user and secret_store.eligible(a_user)), pending=0),
                "rule": sec["approval_rule"], "rule_label": secret_store.RULES[sec["approval_rule"]],
                "accountable": secret_store.person(a_user),
                "approvers": [{**secret_store.person(users[u]), "roles": rs} for u, rs in approvers.items()],
                "my_roles": raci.get(admin.user_id, []),
                "my_level": await secret_store.access_level(conn, sec, me),
                "source_grant": {"id": grant["id"], "expires_at": grant["expires_at"]} if grant else None,
                "source_request": {"id": pending["id"], "approved": pending["approved"], "total": pending["total"],
                                   "expires_at": pending["expires_at"]} if pending else None,
            })
    return {"items": out}


class UseRequestIn(BaseModel):
    secret_id: int
    days: int = Field(ge=1, le=secret_store.MAX_GRANT_DAYS)
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/sources/{source_id}/secret-requests", status_code=201)
async def request_secret_use(source_id: int, body: UseRequestIn, admin: Principal = Depends(require_admin)):
    """Asks the secret's A, R and C to let this source use it ("Brug i kilde"). The saved description must name the
    secret, so the approvers see exactly which hosts the value will be sent to."""
    async with db.connection() as conn:
        src = await db.fetch_one(conn, "select id, key, hosts from data_sources where id = %s", (source_id,))
        if not src:
            raise HTTPException(404, "Kilden findes ikke")
        if not await db.fetch_one(conn, "select 1 from data_source_secrets where source_id = %s and secret_id = %s",
                                  (source_id, body.secret_id)):
            raise HTTPException(409, "Kilden nævner ikke hemmeligheden. Skriv den i beskrivelsen og gem kilden først")
        secret = await secret_store.get(conn, body.secret_id, lock=True)
        if await source_secrets.source_grant(conn, secret["id"], source_id):
            raise HTTPException(409, "Kilden har allerede adgang")
        reason = f"{body.reason.strip()}\n\nKilde {src['key']} sender værdien til {', '.join(src['hosts'])}."
        row = await secret_store.create_request(conn, secret, requester_id=admin.user_id, grantee_user_id=None,
                                                grantee_group_id=None, grantee_source_id=source_id, level="use",
                                                days=body.days, reason=reason[:1000], meta=admin.meta)
        await audit(conn, "secret_use_requested", actor_id=admin.user_id, meta=admin.meta, table="data_sources",
                    row_id=source_id, secret_id=secret["id"], request_id=row["id"], days=body.days)
    return {"id": row["id"], "status": row["status"]}


# ── Connections: APIs called from code and API sources (Udforsk → Forbindelser) ──

def _endpoint_out(method: str | None, path: str, label: str, called_from: str, row: dict | None) -> dict:
    return {"method": method, "path": path, "label": label, "called_from": called_from,
            "calls": row["calls"] if row else 0, "errors": row["errors"] if row else 0,
            "p95_ms": round(row["p95_ms"]) if row and row["p95_ms"] is not None else None,
            "last_at": row["last_at"] if row else None, "last_error_at": row["last_error_at"] if row else None}


def _host(url: str | None) -> str | None:
    try:
        return urlsplit(url).hostname if url else None
    except ValueError:
        return None


@router.get("/connections")
async def connections(hours: int = 24):
    """Every outside API the instance talks to: the ones called from code (app/integrations.py) and the API sources
    made in Forbind API, with keys (names and whether they are set, never values), endpoints and calls in the last
    `hours` from the events `api.call`."""
    hours = max(1, min(hours, 168))
    async with db.connection() as conn:
        code_stats = await integrations.stats(conn, "", hours)
        code = []
        for it in integrations.REGISTRY:
            mine = {ref: r for ref, r in code_stats.items() if ref.startswith(f"{it.key}:")}
            endpoints = []
            for e in it.endpoints:
                rows = [r for ref, r in mine.items() if ref.split(":", 1)[1].endswith(f" {e.path}")
                        and (e.method is None or ref.split(":", 1)[1].startswith(f"{e.method} "))]
                endpoints.append(_endpoint_out(e.method, e.path, e.label, e.called_from,
                                               integrations.summarize(rows) if rows else None))
            other = [r for ref, r in mine.items() if ref.endswith("(andet)")]
            if other:
                endpoints.append(_endpoint_out(None, "(andet)", "Kald, der ikke passer på et kendt endpoint", "—",
                                               integrations.summarize(other)))
            base = it.base()
            code.append({
                "key": it.key, "kind": "code", "name": it.name, "purpose": it.purpose, "files": list(it.files),
                "host": _host(base), "base_url": base if base and base.startswith("https://") else None,
                "active": it.active(), "counted": it.counted, "link": it.link,
                "secrets": [{"name": n, "set": integrations.secret_is_set(n), "where": "env"} for n in it.secrets],
                "settings": [{"name": n, "set": integrations.secret_is_set(n)} for n in it.settings],
                "endpoints": endpoints, **integrations.summarize(list(mine.values())),
            })
        sources = []
        for s in await db.fetch_all(conn, "select id, key, name, hosts, spec_yaml from data_sources order by name"):
            try:
                spec = sources_mod.parse_spec(s["spec_yaml"])
            except SpecError:
                spec = None
            stats = await integrations.stats(conn, f"source:{s['key']}:", hours)
            _, secret_status = await source_secrets.resolve(conn, spec, source_id=s["id"], values_wanted=False) \
                if spec else ({}, [])
            datasets = {r["key"]: r for r in await db.fetch_all(
                conn, "select id, key, name from datasets where source_id = %s", (s["id"],))}
            endpoints = []
            for d in (spec or {}).get("datasets", []):
                row = stats.get(f"source:{s['key']}:GET {d['path']}")
                ds = datasets.get(d["key"])
                endpoints.append({**_endpoint_out("GET", d["path"], d["name"], "Forbind API", row),
                                  "dataset_id": ds["id"] if ds else None})
            token = stats.get(f"source:{s['key']}:POST token")
            if spec and spec["auth"]["type"] == "oauth2":
                endpoints.insert(0, {**_endpoint_out("POST", spec["auth"]["token_url"], "OAuth 2-token", "Forbind API",
                                                     token), "dataset_id": None})
            sources.append({
                "id": s["id"], "key": s["key"], "kind": "source", "name": s["name"], "host": s["hosts"][0] if s["hosts"] else None,
                "hosts": s["hosts"], "auth": spec["auth"]["type"] if spec else None, "valid": spec is not None,
                "active": True, "counted": True, "secrets": [_secret_status_out(x) for x in secret_status],
                "endpoints": endpoints, **integrations.summarize(list(stats.values())),
            })
    return {"hours": hours, "code": code, "sources": sources}
