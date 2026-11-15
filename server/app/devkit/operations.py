"""Operations and the runtime (API-2, DS-9, F-10, F-17 … F-21): one definition that an MCP tool and an API operation
both point to, so MCP and the API always answer alike, and masking happens in one place just before data leaves.

    {"description": "Søg lejemål på adresse eller postnr.",
     "source": {"dataset": 12},                     # a dataset in the catalog: table, foreign table, snapshot or API
     "fields": ["adresse", "postnr", "aarlig_leje", "lejer_navn"],
     "params": [{"name": "postnr", "field": "postnr", "op": "eq", "type": "string", "required": false,
                 "description": "Postnummer"}],
     "filters": [{"field": "status", "op": "eq", "value": "aktiv"}],
     "masking": {"aarlig_leje": "interval"},       # never weaker than the classification (masking.policy_for)
     "sort": "-aarlig_leje", "limit": 50, "max_limit": 200,
     "cache": {"ttl": 300, "swr": 0}}              # seconds; snapshots: until the next refresh

Operations on tables, foreign tables and snapshots become parametrized SQL (names as identifiers, values as %s), run
in a READ ONLY transaction with a short timeout. A parameter may only filter on a field that is shown unmasked.
"""

import time

import psycopg
from fastapi import HTTPException
from psycopg import sql

from app import db
from app.devkit import artifacts, cache, connections, masking

OPS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
TYPES = {"string", "int", "number", "bool", "date"}
TIMEOUT_MS = 5000
SNAPSHOT_TTL = 86_400


class OperationError(HTTPException):
    def __init__(self, text: str, status: int = 422):
        super().__init__(status, text)


async def dataset_with_fields(conn, ref: dict) -> tuple[dict, list[dict], dict | None]:
    if not isinstance(ref, dict) or "dataset" not in ref:
        raise OperationError("source skal være {\"dataset\": id}")
    d = await db.fetch_one(conn, """select d.*, s.key as source_key, s.kind as source_kind, s.spec_yaml from datasets d
                                    left join data_sources s on s.id = d.source_id where d.id = %s""", (int(ref["dataset"]),))
    if d is None:
        raise OperationError("Datasættet findes ikke i kataloget")
    fields = await db.fetch_all(conn, """select id, name, label, description, data_type, classification, personal_data, mask
                                         from dataset_fields where dataset_id = %s order by position, id""", (d["id"],))
    source = {"id": d["source_id"], "key": d["source_key"], "kind": d["source_kind"], "spec_yaml": d["spec_yaml"]} if d["source_id"] else None
    return d, fields, source


def _ptype(data_type: str) -> str:
    t = (data_type or "").lower()
    if t in ("integer", "bigint", "smallint"):
        return "int"
    if t in ("numeric", "real", "double precision", "number"):
        return "number"
    if t in ("boolean", "bool"):
        return "bool"
    if t.startswith("date") or t.startswith("timestamp"):
        return "date"
    return "string"


async def validate(conn, definition: dict) -> dict:
    """Normalizes a definition and computes what the artifact needs: output fields, params and the policy."""
    d, fields, _ = await dataset_with_fields(conn, definition.get("source") or {})
    by_name = {f["name"]: f for f in fields}
    out_names = definition.get("fields") or [f["name"] for f in fields]
    for n in out_names:
        if n not in by_name:
            raise OperationError(f"Feltet {n} findes ikke i {d['name']}")
    try:
        policy = masking.policy_for([by_name[n] for n in out_names], definition.get("masking") or {})
    except masking.MaskingError as e:
        raise OperationError(str(e))
    params = []
    for p in definition.get("params") or []:
        name, field = str(p.get("name") or ""), str(p.get("field") or p.get("name") or "")
        if not name.isidentifier() or len(name) > 48:
            raise OperationError(f"Parameteren {name} har et ugyldigt navn")
        if field not in by_name:
            raise OperationError(f"Parameteren {name} filtrerer på {field}, som ikke findes")
        op = p.get("op", "eq")
        if op not in (*OPS, "contains", "starts_with", "in"):
            raise OperationError(f"Parameteren {name}: ukendt operator {op}")
        f_policy = masking.policy_for([by_name[field]]).get(field)
        if f_policy != "show" or policy.get(field, "show") != "show":
            raise OperationError(f"Parameteren {name} kan ikke filtrere på {field}, fordi feltet er maskeret")
        params.append({"name": name, "field": field, "op": op, "type": p.get("type") or _ptype(by_name[field]["data_type"]),
                       "required": bool(p.get("required")), "description": str(p.get("description") or "")[:300]})
    filters = []
    for f in definition.get("filters") or []:
        if f.get("field") not in by_name or f.get("op", "eq") not in (*OPS, "contains", "starts_with", "in"):
            raise OperationError(f"Filteret på {f.get('field')} er ugyldigt")
        filters.append({"field": f["field"], "op": f.get("op", "eq"), "value": f.get("value")})
    sort = definition.get("sort")
    if sort and sort.lstrip("-") not in out_names:
        raise OperationError("sort skal være et af de udstillede felter")
    if sort and policy.get(sort.lstrip("-")) != "show":
        raise OperationError("Der kan ikke sorteres på et maskeret felt")
    try:
        limit = max(1, min(int(definition.get("limit") or 50), 1000))
        max_limit = max(limit, min(int(definition.get("max_limit") or 200), 1000))
        ttl = max(0, min(int((definition.get("cache") or {}).get("ttl", 0)), 86_400))
        swr = max(0, min(int((definition.get("cache") or {}).get("swr", 0)), 3600))
    except (TypeError, ValueError):
        raise OperationError("limit og cache skal være tal")
    return {"description": str(definition.get("description") or "")[:2000], "source": {"dataset": d["id"]},
            "fields": out_names, "params": params, "filters": filters, "masking": definition.get("masking") or {},
            "sort": sort, "limit": limit, "max_limit": max_limit, "cache": {"ttl": ttl, "swr": swr},
            "mode": "read", "examples": list(definition.get("examples") or [])[:10],
            "_output": [{"name": n, "type": by_name[n]["data_type"], "classification": by_name[n]["classification"],
                         "personal": by_name[n]["personal_data"], "policy": policy[n]} for n in out_names],
            "_dataset": {"id": d["id"], "kind": d["kind"], "name": d["name"], "key": d["key"]}}


def contract(definition: dict) -> dict:
    return {"fields": {o["name"]: {"type": o["type"], "classification": o["classification"], "personal": o["personal"]}
                       for o in definition.get("_output") or []},
            "params": {p["name"]: {"type": p["type"], "required": p["required"]} for p in definition.get("params") or []}}


async def dependencies(conn, definition: dict) -> list[tuple[int, str]]:
    ds = (definition.get("_dataset") or {}).get("id")
    if not ds:
        return []
    r = await db.fetch_one(conn, """select d.key, d.artifact_id, d.pipeline_artifact_id, s.artifact_id as source_artifact
                                    from datasets d left join data_sources s on s.id = d.source_id where d.id = %s""", (ds,))
    if r is None:
        return []
    return [(a, r["key"]) for a in (r["artifact_id"], r["pipeline_artifact_id"], r["source_artifact"]) if a]


artifacts.register("operation", artifacts.TypeSpec(
    contract=contract, dependencies=dependencies,
    other={"description": "Beskrivelse", "cache": "Cache", "limit": "Antal rækker", "sort": "Sortering", "examples": "Eksempler",
           "filters": "Faste filtre", "masking": "Maskering"}))


def param_specs(definition: dict) -> list[dict]:
    """The operation's parameters in the MCP catalog's form (and limit/offset)."""
    kinds = {"int": "int", "number": "string", "bool": "bool", "date": "date", "string": "string"}
    out = [{"name": p["name"], "type": kinds.get(p["type"], "string"), "desc": p["description"] or f"filter på {p['field']}",
            "optional": not p["required"]} for p in definition.get("params") or []]
    return out + [{"name": "limit", "type": "int", "desc": f"højst {definition.get('max_limit', 200)}", "optional": True},
                  {"name": "offset", "type": "int", "desc": "spring over", "optional": True}]


def sensitive_params(definition: dict) -> set[str]:
    """Parameters whose values are masked in logs (MCP-14): those on fields that are not white."""
    cls = {o["name"]: o for o in definition.get("_output") or []}
    out = set()
    for p in definition.get("params") or []:
        o = cls.get(p["field"])
        if o is None or o["classification"] != "white" or o["personal"]:
            out.add(p["name"])
    return out


def _coerce(definition: dict, args: dict) -> dict:
    known = {p["name"]: p for p in definition.get("params") or []}
    out = {}
    for k in args:
        if k not in known and k not in ("limit", "offset"):
            raise OperationError(f"Ukendt parameter: {k}")
    for name, p in known.items():
        v = args.get(name)
        if v in (None, ""):
            if p["required"]:
                raise OperationError(f"Parameteren {name} mangler")
            continue
        try:
            if p["op"] == "in":
                v = [str(x).strip() for x in (v if isinstance(v, list) else str(v).split(","))][:100]
            elif p["type"] == "int":
                v = int(v)
            elif p["type"] == "number":
                v = float(v)
            elif p["type"] == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "ja")
            else:
                v = str(v)[:500]
        except (TypeError, ValueError):
            raise OperationError(f"Parameteren {name} skal være {p['type']}")
        out[name] = v
    for k in ("limit", "offset"):
        if args.get(k) not in (None, ""):
            try:
                out[k] = max(0, int(args[k]))
            except (TypeError, ValueError):
                raise OperationError(f"{k} skal være et tal")
    return out


def _condition(field: str, op: str, value) -> tuple[sql.Composable, list]:
    col = sql.Identifier(field)
    if op == "contains":
        return sql.SQL("{}::text ilike %s").format(col), [f"%{str(value).replace('%', '').replace('_', '')}%"]
    if op == "starts_with":
        return sql.SQL("{}::text ilike %s").format(col), [f"{str(value).replace('%', '').replace('_', '')}%"]
    if op == "in":
        return sql.SQL("{}::text = any(%s)").format(col), [list(value if isinstance(value, list) else [value])]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return sql.SQL("{} {} %s").format(col, sql.SQL(OPS[op])), [value]
    return sql.SQL("{}::text {} %s::text").format(col, sql.SQL(OPS[op])), [value]


async def _query(d: dict, source: dict | None, definition: dict, args: dict, limit: int, offset: int) -> list[dict]:
    conds, params = [], []
    for f in definition["filters"]:
        c, p = _condition(f["field"], f["op"], f["value"])
        conds.append(c)
        params += p
    for p in definition["params"]:
        if p["name"] in args:
            c, v = _condition(p["field"], p["op"], args[p["name"]])
            conds.append(c)
            params += v
    where = (sql.SQL(" where ") + sql.SQL(" and ").join(conds)) if conds else sql.SQL("")
    order = sql.SQL("")
    if definition.get("sort"):
        s = definition["sort"]
        order = sql.SQL(" order by {} {} nulls last").format(sql.Identifier(s.lstrip("-")), sql.SQL("desc" if s.startswith("-") else "asc"))
    cols = sql.SQL(", ").join(sql.Identifier(n) for n in definition["fields"])
    direct = None
    if d["kind"] == "foreign" and source:
        import yaml
        cdef = yaml.safe_load(source["spec_yaml"] or "{}") or {}
        if cdef.get("access") == "direct":
            direct = cdef
    if direct:
        table = sql.Identifier(direct["schema"], d["key"])
    else:
        schema, name = connections.table_ref(d, source)
        table = sql.Identifier(schema, name)
    q = sql.SQL("select {} from {}").format(cols, table) + where + order + sql.SQL(" limit %s offset %s")
    params += [limit, offset]
    try:
        if direct:
            async with db.connection() as conn:
                password = await connections.password_for(conn, source, direct)
            async with await connections.direct(direct, password) as rc:
                return await db.fetch_all(rc, q, params)
        async with db.analytics_connection() as conn:
            await conn.execute("set transaction read only")
            await conn.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(f"{TIMEOUT_MS}ms")))
            return await db.fetch_all(conn, q, params)
    except psycopg.errors.QueryCanceled:
        raise OperationError("Opslaget tog for lang tid og blev stoppet", 424)
    except psycopg.Error as e:
        raise OperationError(f"Kilden afviste opslaget: {str(e).splitlines()[0][:160] if str(e) else type(e).__name__}", 424)


async def _query_api(d: dict, definition: dict, args: dict, limit: int, offset: int, principal) -> list[dict]:
    from app.routes import data as data_routes

    filters = [data_routes.FilterIn(field=p["field"], op="eq", value=str(args[p["name"]])) for p in definition["params"]
               if p["name"] in args and p["op"] == "eq"]
    filters += [data_routes.FilterIn(field=f["field"], op="eq", value=str(f["value"])) for f in definition["filters"] if f["op"] == "eq"]
    async with db.connection() as conn:
        full = await data_routes._dataset(conn, d["id"])
        fields = await data_routes._fields(conn, d["id"])
    body = data_routes.QueryIn(filters=filters, limit=min(limit, 200), offset=offset)
    result = await data_routes._query_api(full, fields, body, None, False, principal)
    return [{n: r.get(n) for n in definition["fields"]} for r in result["rows"]]


def _plain(v):
    if isinstance(v, (bytes, memoryview)):
        return "<binær>"
    if isinstance(v, str) and len(v) > 2000:
        return v[:2000] + "…"
    return v


async def execute(artifact: dict, version: dict, args: dict | None, *, principal=None, user_id: int | None = None,
                  use_cache: bool = True) -> dict:
    """Runs one operation: validate the arguments, read through the cache, query the source, mask, store masked.
    Returns {rows, count, hasMore, masked, cache, ageS, ms, version}."""
    started = time.monotonic()
    definition = version["definition"]
    args = _coerce(definition, args or {})
    limit = min(args.pop("limit", definition["limit"]) or definition["limit"], definition["max_limit"])
    offset = args.pop("offset", 0)
    async with db.connection() as conn:
        d, fields, source = await dataset_with_fields(conn, definition["source"])
    by_name = {f["name"]: f for f in fields}
    missing = [n for n in definition["fields"] if n not in by_name]
    if missing:
        raise OperationError(f"Datasættet har ikke længere {', '.join(missing)}. Operationen kræver review", 409)
    # The policy is computed from the catalog *now* and the approved version's choices: a field the catalog made
    # stricter since is masked harder; a weaker choice is refused (F-10).
    try:
        policy = masking.policy_for([by_name[n] for n in definition["fields"]], definition.get("masking") or {})
    except masking.MaskingError as e:
        raise OperationError(f"Maskeringen passer ikke længere til kataloget: {e}", 409)
    ttl = definition["cache"]["ttl"] or (SNAPSHOT_TTL if d["kind"] == "snapshot" else 0)
    swr = definition["cache"]["swr"]
    gen = f"{await cache.generation(f'artifact:{artifact['id']}')}.{d['generation']}"
    vstr = artifacts.version_str(version) or "draft"
    key = cache.make_key(artifact_id=artifact["id"], version=vstr if version["status"] == "published" else f"draft{version['id']}",
                         step="op", params={**args, "_l": limit, "_o": offset}, gen=gen, user_id=None)
    status, age = "miss", None
    if use_cache and ttl:
        value, status, age = await cache.get(key, ttl_s=ttl, max_stale_s=swr)
        if value is not None:
            return {**value, "cache": status, "ageS": round(age or 0), "ms": int((time.monotonic() - started) * 1000), "version": vstr}
    if d["kind"] in ("api", "live"):
        raw = await _query_api(d, definition, args, limit + 1, offset, principal)
    else:
        raw = await _query(d, source, definition, args, limit + 1, offset)
    rows = masking.mask_rows([{k: _plain(v) for k, v in r.items()} for r in raw[:limit]], policy)
    result = {"rows": rows, "count": len(rows), "hasMore": len(raw) > limit, "masked": masking.masked_fields(policy),
              "columns": [{"name": n, "classification": by_name[n]["classification"], "policy": policy[n],
                           "label": by_name[n]["label"] or n} for n in definition["fields"] if policy[n] not in ("remove", "aggregate")]}
    if use_cache and ttl:
        await cache.put(key, result, ttl_s=ttl, max_stale_s=swr)
    return {**result, "cache": "miss" if ttl else None, "ageS": 0, "ms": int((time.monotonic() - started) * 1000), "version": vstr}


async def run_published(slug_or_id, args: dict | None, *, principal=None) -> dict:
    async with db.connection() as conn:
        a = await artifacts.get(conn, int(slug_or_id)) if str(slug_or_id).isdigit() else await artifacts.by_slug(conn, "operation", slug_or_id)
        if a is None or a["type"] != "operation":
            raise OperationError("Operationen findes ikke", 404)
        if a["status"] == "disabled":
            raise OperationError("Operationen er slået fra", 409)
        v = await artifacts.current(conn, a)
    if v is None:
        raise OperationError("Operationen har ingen udgivet version", 409)
    return await execute(a, v, args, principal=principal)
