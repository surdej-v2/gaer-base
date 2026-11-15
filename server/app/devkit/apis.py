"""API-studie (API-1 … API-12): REST APIs on /gaer/v<major>/<slug> over the same operations as MCP.

    {"name": "Lejedata", "description": "…", "cors": ["https://erp.example.dk"], "limits": {"per_key": 60},
     "sunset_months": 6,
     "operations": [{"method": "GET", "path": "/lejemaal", "operation": 41, "level": "white", "summary": "Søg lejemål"},
                    {"method": "GET", "path": "/lejemaal/:id", "operation": 42, "level": "white"},
                    {"method": "POST", "path": "/lejemaal/soeg", "operation": 41, "level": "yellow"}]}

- **Routing** (API-1, API-9): /gaer/v<major>/<slug>/<path> finds the published version of exactly that major
  (dev_api_majors), so two majors run side by side while the old one is phased out. A new major gives the previous one
  a sunset (at least 6 months; Accountable may extend). Answers from an old major carry `Deprecation` and `Sunset`;
  after the sunset it answers 410.
- **Operations** (API-2, API-3): each points to an operation or a multisource source — the same definition an MCP tool
  uses. GET reads with filters (the operation's parameters), sorting and paging (`limit`, `offset`). POST takes the
  parameters as a JSON body (a search). Acting operations (PUT, PATCH, DELETE) need code operations, which Gær
  Developer does not have yet; they answer 405.
- **Access** (API-4, API-6, API-7, API-8): an API key (`Authorization: Bearer gk_…`), an access token from
  /gaer/oauth/token (client credentials with a key of type client_credentials), or a user's OAuth token (the MCP
  login). Keys are hashed (SHA-256, constant-time comparison), have scopes per operation, an expiry and an optional IP
  range. Yellow and red operations need a key whose scopes name exactly that operation (no wildcard), and a POST to
  them needs an `Idempotency-Key` (the answer is kept a day). Users: external users only white operations; yellow and
  red need a key.
- **Limits and caching** (API-11, API-12): token buckets per key and operation → 429 + Retry-After. Every answer has an
  ETag (If-None-Match → 304) and `Cache-Control: private` (keyed answers must never sit in a shared cache), plus
  max-age from the operation's cache TTL. CORS per API.
- **OpenAPI 3.1** (API-5) is generated from the definition with fields, classification and masking.
"""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets as pysecrets
from datetime import datetime, timezone
from email.utils import format_datetime

from fastapi import HTTPException

from app import db, jobs
from app.devkit import artifacts, masking, operations

PATH_RE = re.compile(r"^(/[a-z0-9_-]+|/:[a-z_][a-z0-9_]*)+$|^/$")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
LEVELS = ("white", "green", "yellow", "red")
TOKEN_MINUTES = 60
MIN_SUNSET_MONTHS = 6


# ── Definition ──────────────────────────────────────────────────────────────

async def validate(conn, definition: dict) -> dict:
    ops, seen = [], set()
    for o in definition.get("operations") or []:
        method, path = str(o.get("method") or "GET").upper(), str(o.get("path") or "")
        if method not in METHODS or not PATH_RE.match(path):
            raise HTTPException(422, f"{method} {path}: metoden skal være GET, POST, PUT, PATCH eller DELETE, og stien som /lejemaal/:id")
        if (method, path) in seen:
            raise HTTPException(422, f"{method} {path} findes to gange")
        seen.add((method, path))
        target = await db.fetch_one(conn, f"""select {artifacts.COLUMNS} from dev_artifacts a where a.id = %s
                                              and a.type in ('operation', 'multisource')""", (int(o.get("operation") or 0),))
        if target is None:
            raise HTTPException(422, f"{method} {path}: operationen findes ikke")
        v = await artifacts.current(conn, target)
        if v is None:
            raise HTTPException(422, f"{method} {path}: operationen har ingen udgivet version")
        level = o.get("level") or "white"
        if level not in LEVELS:
            raise HTTPException(422, f"{method} {path}: niveau skal være hvid, grøn, gul eller rød")
        params = [p["name"] for p in v["definition"].get("params") or []]
        for name in re.findall(r":([a-z_][a-z0-9_]*)", path):
            if name not in params:
                raise HTTPException(422, f"{method} {path}: :{name} er ikke en parameter på operationen")
        ops.append({"method": method, "path": path, "operation": target["id"], "operation_slug": target["slug"], "kind": target["type"],
                    "level": level, "summary": str(o.get("summary") or v["definition"].get("description") or "")[:300],
                    "_contract": v["contract"], "_definition": {k: v["definition"].get(k) for k in ("params", "_output", "examples", "cache", "max_limit")}})
    cors = [str(x) for x in definition.get("cors") or []][:20]
    for origin in cors:
        if not re.match(r"^https?://[A-Za-z0-9.-]+(:\d+)?$", origin):
            raise HTTPException(422, f"CORS: {origin} skal være en oprindelse som https://erp.example.dk")
    months = max(MIN_SUNSET_MONTHS, int(definition.get("sunset_months") or MIN_SUNSET_MONTHS))
    limits = {k: int(v) for k, v in (definition.get("limits") or {}).items() if k in ("per_key", "error_alarm_pct") and v}
    return {"name": str(definition.get("name") or "")[:120], "description": str(definition.get("description") or "")[:2000],
            "operations": ops, "cors": cors, "limits": limits, "sunset_months": months}


def op_key(o: dict) -> str:
    return f"{o['method']} {o['path']}"


def contract(definition: dict) -> dict:
    fields, params = {}, {}
    for o in definition.get("operations") or []:
        c = o.get("_contract") or {}
        for k, v in (c.get("fields") or {}).items():
            fields[f"{op_key(o)}.{k}"] = v
        for k, v in (c.get("params") or {}).items():
            params[f"{op_key(o)}.{k}"] = v
    return {"items": [op_key(o) for o in definition.get("operations") or []], "fields": fields, "params": params}


async def dependencies(conn, definition: dict) -> list[tuple[int, str]]:
    return [(o["operation"], op_key(o)) for o in definition.get("operations") or []]


async def on_publish(conn, artifact: dict, v: dict, prev: dict | None) -> None:
    """API-9: a new major gets its own line; the one before it is phased out after sunset_months."""
    await conn.execute("""insert into dev_api_majors (api_artifact_id, major, version_id) values (%s, %s, %s)
                          on conflict (api_artifact_id, major) do update set version_id = excluded.version_id""",
                       (artifact["id"], v["major"], v["id"]))
    if prev and prev["major"] < v["major"]:
        months = v["definition"].get("sunset_months", MIN_SUNSET_MONTHS)
        await conn.execute("""update dev_api_majors set sunset_at = now() + make_interval(months => %s)
                              where api_artifact_id = %s and major = %s and sunset_at is null""", (months, artifact["id"], prev["major"]))
        for owner in await key_owners(conn, artifact["id"]):
            await artifacts.notify(conn, owner, "api_major", f"{artifact['slug']} v{v['major']} er udgivet",
                                   f"v{prev['major']} kører mindst {months} måneder endnu, derefter lukkes den.",
                                   link=f"/developer/api/{artifact['id']}", artifact_id=artifact["id"])


artifacts.register("api", artifacts.TypeSpec(contract=contract, dependencies=dependencies, on_publish=on_publish,
                                             other={"description": "Beskrivelse", "cors": "CORS", "limits": "Rate limits",
                                                    "sunset_months": "Udfasning", "name": "Navn"}))
artifacts.register("api_key", artifacts.TypeSpec(contract=lambda d: {"items": sorted(d.get("scopes") or [])},
                                                 other={"name": "Navn", "expires_at": "Udløb", "ip_allow": "IP-begrænsning"}))


async def key_owners(conn, api_artifact_id: int) -> list[int]:
    rows = await db.fetch_all(conn, """select distinct r.user_id from dev_api_keys k join dev_artifact_roles r on r.artifact_id = k.artifact_id
                                       where k.api_artifact_id = %s and k.status <> 'revoked' and r.role in ('A', 'R')""", (api_artifact_id,))
    return [r["user_id"] for r in rows]


# ── Keys ────────────────────────────────────────────────────────────────────

def _hash(secret: str) -> str:
    return hashlib.sha256(b"gaer-api-key\0" + secret.encode()).hexdigest()


def new_key() -> tuple[str, str]:
    return "gk_" + pysecrets.token_hex(8), pysecrets.token_urlsafe(32)


def scope_allows(scopes: list[str], o: dict, *, exact: bool) -> bool:
    key = op_key(o)
    for s in scopes:
        if s == key:
            return True
        if exact:
            continue
        if s == "*" or (s.endswith("*") and key.startswith(s[:-1])) or s == f"{o['method']} *":
            return True
    return False


async def key_by_token(token: str, ip: str | None) -> dict | None:
    """`gk_<id>.<secret>` (an API key) or `gt_<token>` (from client credentials). Constant-time on the secret."""
    async with db.connection() as conn:
        if token.startswith("gt_"):
            row = await db.fetch_one(conn, """select k.*, t.scopes as token_scopes from dev_api_tokens t join dev_api_keys k on k.id = t.key_id
                                              where t.token_hash = %s and t.expires_at > now()""", (_hash(token),))
            if row:
                row["scopes"] = row["token_scopes"] or row["scopes"]
        else:
            key_id, _, secret = token.partition(".")
            row = await db.fetch_one(conn, "select * from dev_api_keys where key_id = %s", (key_id,))
            if row is None or not hmac.compare_digest(row["secret_hash"], _hash(secret)):
                return None
        if row is None or row["status"] == "revoked" or row["expires_at"] <= datetime.now(timezone.utc):
            return None
        if row["ip_allow"] and ip:
            addr = ipaddress.ip_address(ip)
            if not any(addr in ipaddress.ip_network(str(n), strict=False) for n in row["ip_allow"]):
                return None
        await conn.execute("update dev_api_keys set last_used_at = now() where id = %s", (row["id"],))
    return row


async def client_credentials(client_id: str, client_secret: str, scope: str | None) -> dict:
    row = await key_by_token(f"{client_id}.{client_secret}", None)
    if row is None or row["kind"] != "client_credentials":
        raise HTTPException(401, "invalid_client")
    wanted = [s for s in (scope or "").split() if s]
    scopes = [s for s in wanted if s in row["scopes"]] if wanted else row["scopes"]
    token = "gt_" + pysecrets.token_urlsafe(32)
    async with db.connection() as conn:
        await conn.execute("insert into dev_api_tokens (token_hash, key_id, scopes, expires_at) values (%s, %s, %s, now() + make_interval(mins => %s))",
                           (_hash(token), row["id"], scopes, TOKEN_MINUTES))
    return {"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_MINUTES * 60, "scope": " ".join(scopes)}


# ── Routing ─────────────────────────────────────────────────────────────────

def match(definition: dict, method: str, path: str) -> tuple[dict | None, dict, bool]:
    """(operation, path parameters, whether the path exists for another method)."""
    path = "/" + path.strip("/")
    other = False
    for o in definition["operations"]:
        parts, want = o["path"].strip("/").split("/"), path.strip("/").split("/")
        if len(parts) != len(want) and not (o["path"] == "/" and path == "/"):
            continue
        params, ok = {}, True
        for a, b in zip(parts, want):
            if a.startswith(":"):
                params[a[1:]] = b
            elif a != b:
                ok = False
                break
        if ok:
            if o["method"] == method:
                return o, params, False
            other = True
    return None, {}, other


async def line(slug: str, major: int) -> tuple[dict, dict, dict] | None:
    async with db.connection() as conn:
        a = await artifacts.by_slug(conn, "api", slug)
        if a is None or a["status"] == "disabled":
            return None
        m = await db.fetch_one(conn, "select * from dev_api_majors where api_artifact_id = %s and major = %s", (a["id"], major))
        if m is None or m["version_id"] is None:
            return None
        v = await artifacts.version(conn, m["version_id"])
    return a, v, m


def etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def deprecation_headers(m: dict) -> dict:
    if not m["sunset_at"]:
        return {}
    return {"Deprecation": f"@{int(m['published_at'].timestamp())}", "Sunset": format_datetime(m["sunset_at"].astimezone(timezone.utc), usegmt=True)}


async def run(o: dict, args: dict, principal) -> dict:
    async with db.connection() as conn:
        target = await artifacts.get(conn, o["operation"])
        v = await artifacts.current(conn, target)
    if v is None or target["status"] == "disabled":
        raise HTTPException(409, "Operationen er ikke tilgængelig")
    if target["type"] == "multisource":
        from app.devkit import multisource
        out = await multisource.execute(target, v, args, principal=principal)
        return {"data": out["rows"], "count": out["count"], "partial": out["partial"], "cache": None, "ttl": 0}
    out = await operations.execute(target, v, args, principal=principal)
    ttl = (v["definition"].get("cache") or {}).get("ttl") or 0
    return {"data": out["rows"], "count": out["count"], "hasMore": out["hasMore"], "masked": out["masked"],
            "cache": {"status": out["cache"], "ageSeconds": out["ageS"]} if out["cache"] in ("hit", "stale") else None, "ttl": ttl}


async def log(*, api: str, major: int, method: str, path: str, key_id: str | None, user_id: int | None, status: int, ms: int,
              cache_status: str | None) -> None:
    async with db.connection() as conn:
        await conn.execute("""insert into dev_api_log (api, major, method, path, key_id, user_id, status, duration_ms, cache)
                              values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", (api, major, method, path, key_id, user_id, status, ms, cache_status))


def masked_args(o: dict, args: dict) -> dict:
    return masking.mask_args(args, operations.sensitive_params(o.get("_definition") or {})) or {}


# ── OpenAPI 3.1 (API-5) ─────────────────────────────────────────────────────

JSON_TYPES = {"int": "integer", "number": "number", "bool": "boolean", "date": "string", "string": "string"}


def _field_schema(f: dict) -> dict:
    t = (f.get("type") or "text").lower()
    typ = "integer" if t in ("integer", "bigint", "smallint") else "number" if t in ("numeric", "real", "double precision") else \
        "boolean" if t.startswith("bool") else "string"
    policy = f.get("policy") or masking.default_for({"classification": f.get("classification"), "personal_data": f.get("personal")})
    schema = {"type": [typ, "null"] if policy in ("show", "round") else ["string", "null"],
              "description": f"Klassifikation: {masking.CLASS_DA.get(f.get('classification'), '?')}"
                             + ("; persondata" if f.get("personal") else "") + f"; maskering: {masking.LABELS.get(policy, policy)}",
              "x-gaer-classification": f.get("classification"), "x-gaer-masking": policy}
    return schema


def openapi(a: dict, v: dict, origin: str) -> dict:
    d = v["definition"]
    base = f"{origin}/gaer/v{v['major']}/{a['slug']}"
    paths: dict = {}
    for o in d["operations"]:
        od = o.get("_definition") or {}
        path_names = re.findall(r":([a-z_][a-z0-9_]*)", o["path"])
        params = [{"name": p["name"], "in": "path" if p["name"] in path_names else "query", "required": p["name"] in path_names or p.get("required", False),
                   "description": p.get("description") or f"Filter på {p['field']}", "schema": {"type": JSON_TYPES.get(p.get("type"), "string")}}
                  for p in od.get("params") or []]
        if o["method"] == "GET":
            params += [{"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": od.get("max_limit") or 200}},
                       {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0}}]
        item = {"type": "object", "properties": {f["name"]: _field_schema(f) for f in od.get("_output") or []}}
        op = {"operationId": f"{o['method'].lower()}_{re.sub(r'[^a-z0-9]+', '_', o['path']).strip('_') or 'root'}",
              "summary": o["summary"], "x-gaer-level": o["level"], "x-gaer-operation": o["operation_slug"],
              "parameters": [p for p in params if o["method"] == "GET" or p["in"] == "path"],
              "security": [{"apiKey": []}, {"clientCredentials": []}],
              "responses": {"200": {"description": "OK", "headers": {"ETag": {"schema": {"type": "string"}}},
                                    "content": {"application/json": {"schema": {"type": "object", "properties": {
                                        "data": {"type": "array", "items": item}, "count": {"type": "integer"},
                                        "hasMore": {"type": "boolean"}, "masked": {"type": "array", "items": {"type": "string"}}}},
                                        **({"examples": {f"eksempel{i + 1}": {"summary": json.dumps(e.get('args', e), ensure_ascii=False)[:120], "value": {}}
                                                         for i, e in enumerate(od.get("examples") or [])}} if od.get("examples") else {})}}},
                            "304": {"description": "Uændret (If-None-Match)"}, "401": {"description": "Mangler eller ugyldig nøgle"},
                            "403": {"description": "Nøglen dækker ikke operationen"}, "429": {"description": "For mange kald (Retry-After)"}}}
        if o["method"] != "GET":
            op["requestBody"] = {"content": {"application/json": {"schema": {"type": "object", "properties": {
                p["name"]: {"type": JSON_TYPES.get(p.get("type"), "string")} for p in od.get("params") or []}}}}}
            op["parameters"].append({"name": "Idempotency-Key", "in": "header", "required": o["level"] in ("yellow", "red"),
                                     "schema": {"type": "string"}})
        oa_path = re.sub(r":([a-z_][a-z0-9_]*)", r"{\1}", o["path"])
        paths.setdefault(oa_path, {})[o["method"].lower()] = op
    return {"openapi": "3.1.0", "info": {"title": d.get("name") or a["name"], "version": artifacts.version_str(v),
                                          "description": d.get("description") or ""},
            "servers": [{"url": base}], "paths": paths,
            "components": {"securitySchemes": {
                "apiKey": {"type": "http", "scheme": "bearer", "description": "API-nøgle: gk_<id>.<hemmelighed>"},
                "clientCredentials": {"type": "oauth2", "flows": {"clientCredentials": {"tokenUrl": f"{origin}/gaer/oauth/token", "scopes": {
                    op_key(o): o["summary"] or op_key(o) for o in d["operations"]}}}}}}}


def client_code(spec: dict, lang: str) -> str:
    """A small client generated from the OpenAPI document (API-5)."""
    base = spec["servers"][0]["url"]
    lines = []
    if lang == "python":
        lines += ["import httpx", "", f"BASE = \"{base}\"", "", "class Client:",
                  "    def __init__(self, key: str):", "        self.http = httpx.Client(base_url=BASE, headers={\"Authorization\": f\"Bearer {key}\"})", ""]
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                names = [p["name"] for p in op["parameters"] if p["in"] in ("path", "query")]
                sig = ", ".join(["self"] + [f"{n}=None" for n in names])
                url = path.replace("{", "{").replace("}", "}")
                lines += [f"    def {op['operationId']}({sig}):",
                          f"        params = {{k: v for k, v in dict({', '.join(f'{n}={n}' for n in names if '{' + n + '}' not in path)}).items() if v is not None}}",
                          f"        r = self.http.{method}(f\"{url}\", {'params' if method == 'get' else 'json'}=params)",
                          "        r.raise_for_status()", "        return r.json()", ""]
    else:
        lines += [f"const BASE = \"{base}\";", "", "export function client(key: string) {",
                  "  const call = async (method: string, path: string, params: Record<string, unknown> = {}) => {",
                  "    const q = method === \"GET\" ? \"?\" + new URLSearchParams(Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])) : \"\";",
                  "    const r = await fetch(BASE + path + q, { method, headers: { Authorization: `Bearer ${key}`, \"Content-Type\": \"application/json\" },",
                  "      body: method === \"GET\" ? undefined : JSON.stringify(params) });",
                  "    if (!r.ok) throw new Error(`${r.status}`);", "    return r.json();", "  };", "  return {"]
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                p = re.sub(r"\{(\w+)\}", r"${params.\1}", path)
                lines.append(f"    {op['operationId']}: (params: Record<string, unknown> = {{}}) => call(\"{method.upper()}\", `{p}`, params),")
        lines += ["  };", "}", ""]
    return "\n".join(lines)


# ── Phasing out old majors (API-9) ──────────────────────────────────────────

@jobs.handler("developer.api_sunset")
async def sunset_job(payload: dict, job: dict) -> dict:
    sent = 0
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select m.*, a.slug, a.name from dev_api_majors m join dev_artifacts a on a.id = m.api_artifact_id
                                           where m.sunset_at is not null and m.sunset_at > now()""")
        for m in rows:
            days = (m["sunset_at"] - datetime.now(timezone.utc)).days
            for mark in (90, 30, 7):
                if days <= mark and mark not in m["notified"]:
                    for owner in await key_owners(conn, m["api_artifact_id"]):
                        await artifacts.notify(conn, owner, "api_sunset", f"{m['slug']} v{m['major']} lukker om {max(days, 0)} dage",
                                               f"Skift til den nyeste major før {m['sunset_at']:%d.%m.%Y}.",
                                               link=f"/developer/api/{m['api_artifact_id']}", artifact_id=m["api_artifact_id"])
                        sent += 1
                    await conn.execute("update dev_api_majors set notified = array_append(notified, %s) where api_artifact_id = %s and major = %s",
                                       (mark, m["api_artifact_id"], m["major"]))
                    break
    return {"sent": sent}



# ── Monitoring (API-11): the same minute aggregates as MCP, per API ─────────

from app.devkit import mcp_studio  # noqa: E402

mcp_studio.AGG_SOURCES.append("""
    insert into dev_call_metrics (scope, minute, calls, errors, p50_ms, p95_ms, cache_hits, cache_lookups)
    select 'api:' || api, date_trunc('minute', ts), count(*), count(*) filter (where status >= 400),
           percentile_cont(0.5) within group (order by duration_ms)::int, percentile_cont(0.95) within group (order by duration_ms)::int,
           count(*) filter (where cache in ('hit', 'stale')), count(*) filter (where cache is not null)
    from dev_api_log where ts >= %s and ts < date_trunc('minute', now())
    group by 1, 2
    on conflict (scope, minute) do update set calls = excluded.calls, errors = excluded.errors, p50_ms = excluded.p50_ms,
        p95_ms = excluded.p95_ms, cache_hits = excluded.cache_hits, cache_lookups = excluded.cache_lookups""")
