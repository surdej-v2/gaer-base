"""/api/admin/dev: API-studie (API-1 … API-12) — APIs over operations, API keys (only Udvikler-admin and Fuld create,
rotate and revoke them: admin_roles.DEV_ADMIN_WRITES), the OpenAPI document and generated clients, the test console and
monitoring per key. Every change is audited.
"""

import ipaddress
import json
import shlex
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import config, db
from app.devkit import apis, artifacts, mcp_studio
from app.logs import audit
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/dev", tags=["developer"], dependencies=[Depends(require_admin)])


def _origin() -> str:
    return config.settings.origin.rstrip("/")


@router.get("/apis")
async def list_apis():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select {artifacts.COLUMNS}, v.major, v.minor, v.patch, v.definition from dev_artifacts a
                                            left join dev_artifact_versions v on v.id = a.current_version_id where a.type = 'api' order by a.name""")
        majors = await db.fetch_all(conn, "select api_artifact_id, major, sunset_at from dev_api_majors order by major")
    by: dict[int, list] = {}
    for m in majors:
        by.setdefault(m["api_artifact_id"], []).append(m)
    return {"items": [{**artifacts.out(r, r if r["major"] is not None else None), "operations": len((r["definition"] or {}).get("operations") or []),
                       "url": f"{_origin()}/gaer/v{r['major']}/{r['slug']}" if r["major"] is not None else None,
                       "majors": by.get(r["id"], [])} for r in rows]}


class ApiIn(BaseModel):
    slug: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9-]{1,39}$")
    name: str = Field(min_length=1, max_length=120)
    definition: dict
    approval_flow: bool = False


@router.post("/apis", status_code=201)
async def create_api(body: ApiIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        d = await apis.validate(conn, {**body.definition, "name": body.definition.get("name") or body.name})
        a = await artifacts.create(conn, type_="api", slug=body.slug, name=body.name, description=d["description"], definition=d,
                                   actor_id=p.user_id, meta=p.meta, approval_flow=body.approval_flow)
    return {"id": a["id"]}


@router.get("/apis/{artifact_id}")
async def api_detail(artifact_id: int):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        if a["type"] != "api":
            raise HTTPException(404, "API'et findes ikke")
        detail = await artifacts.detail(conn, artifact_id)
        majors = await db.fetch_all(conn, """select m.major, m.published_at, m.sunset_at, v.major as vmajor, v.minor, v.patch from dev_api_majors m
                                             left join dev_artifact_versions v on v.id = m.version_id where m.api_artifact_id = %s order by m.major desc""",
                                    (artifact_id,))
        keys = await _keys(conn, artifact_id)
        cur = await artifacts.current(conn, a)
    working = detail["draft"] or detail["current"]
    ops = [{k: v for k, v in o.items() if not k.startswith("_")} for o in working["definition"]["operations"]]
    return {"artifact": detail, "operations": ops, "definition": {k: v for k, v in working["definition"].items() if k != "operations"},
            "majors": [{"major": m["major"], "version": f"{m['vmajor']}.{m['minor']}.{m['patch']}" if m["vmajor"] is not None else None,
                        "publishedAt": m["published_at"], "sunsetAt": m["sunset_at"], "url": f"{_origin()}/gaer/v{m['major']}/{a['slug']}"}
                       for m in majors],
            "keys": keys, "openapi": apis.openapi(a, cur, _origin()) if cur else None}


class ApiEdit(BaseModel):
    definition: dict
    note: str = Field(default="", max_length=2000)
    submit: bool = False
    reason: str = Field(default="", max_length=2000)


@router.put("/apis/{artifact_id}")
async def save_api(artifact_id: int, body: ApiEdit, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id, lock=True)
        if a["type"] != "api":
            raise HTTPException(404, "API'et findes ikke")
        d = await apis.validate(conn, body.definition)
        v = await artifacts.save_draft(conn, a, d, actor_id=p.user_id, meta=p.meta, note=body.note)
        if body.submit:
            v = await artifacts.submit(conn, a, actor_id=p.user_id, meta=p.meta, note=body.note, reason=body.reason)
    return {"version": artifacts.version_out(v)}


class SunsetIn(BaseModel):
    months: int = Field(ge=1, le=60)


@router.post("/apis/{artifact_id}/majors/{major}/extend")
async def extend_sunset(artifact_id: int, major: int, body: SunsetIn, p: Principal = Depends(require_admin)):
    """API-9: Accountable can extend an old major's life (never shorten it here)."""
    async with db.connection() as conn:
        if not await artifacts.is_owner(conn, artifact_id, p.user_id, ("A",)):
            raise HTTPException(403, "Kun API'ets Accountable kan forlænge perioden")
        row = await db.fetch_one(conn, """update dev_api_majors set sunset_at = sunset_at + make_interval(months => %s), notified = '{}'
                                          where api_artifact_id = %s and major = %s and sunset_at is not null returning sunset_at""",
                                 (body.months, artifact_id, major))
        if row is None:
            raise HTTPException(404, "Den major udfases ikke")
        await audit(conn, "dev.api.sunset_extended", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=artifact_id,
                    major=major, months=body.months)
    return {"sunsetAt": row["sunset_at"]}


@router.get("/apis/{artifact_id}/client")
async def client(artifact_id: int, lang: str = "typescript"):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        v = await artifacts.current(conn, a)
    if v is None:
        raise HTTPException(409, "API'et har ingen udgivet version")
    return {"lang": lang, "code": apis.client_code(apis.openapi(a, v, _origin()), "python" if lang == "python" else "typescript")}


class ConsoleIn(BaseModel):
    method: str = Field(default="GET", pattern="^(GET|POST)$")
    path: str = Field(max_length=500)
    query: dict = Field(default_factory=dict)
    body: dict = Field(default_factory=dict)
    major: int | None = None
    as_user_id: int | None = None


@router.post("/apis/{artifact_id}/console")
async def console(artifact_id: int, body: ConsoleIn, p: Principal = Depends(require_admin)):
    """API-10: calls an operation as yourself or a test account (external test accounts only reach white operations),
    and shows the request as curl. No key is needed or shown."""
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        cur = await artifacts.current(conn, a)
    if cur is None:
        raise HTTPException(409, "API'et har ingen udgivet version")
    major = body.major or cur["major"]
    found = await apis.line(a["slug"], major)
    if found is None:
        raise HTTPException(404, "Den major findes ikke")
    _, v, m = found
    o, params, _ = apis.match(v["definition"], body.method, body.path)
    if o is None:
        raise HTTPException(404, f"{body.method} {body.path} findes ikke i v{major}")
    who = await mcp_studio.test_principal(p, body.as_user_id)
    async with db.connection() as conn:
        u = await db.fetch_one(conn, "select audience from users where id = %s", (who.user_id,))
    started = time.monotonic()
    if o["level"] != "white" and (u or {}).get("audience") != "internal":
        status, payload = 403, {"error": "Eksterne brugere har kun adgang til operationer på hvidt niveau"}
    else:
        try:
            payload = await apis.run(o, {**(body.query if body.method == "GET" else body.body), **params},
                                     SimpleNamespace(meta=p.meta, user_id=who.user_id))
            payload.pop("ttl", None)
            status = 200
        except HTTPException as e:
            status, payload = e.status_code, {"error": e.detail}
    url = f"{_origin()}/gaer/v{major}/{a['slug']}/{body.path.lstrip('/')}"
    if body.method == "GET" and body.query:
        from urllib.parse import urlencode
        url += "?" + urlencode(body.query)
    curl = f"curl -H \"Authorization: Bearer $NOEGLE\" {shlex.quote(url)}" if body.method == "GET" else \
        f"curl -X POST -H \"Authorization: Bearer $NOEGLE\" -H \"Content-Type: application/json\" -d {shlex.quote(json.dumps(body.body))} {shlex.quote(url)}"
    text = json.dumps(payload, ensure_ascii=False, default=str).encode()
    return {"status": status, "ms": int((time.monotonic() - started) * 1000), "body": payload, "curl": curl, "as": who.display_name,
            "headers": {"ETag": apis.etag(text), "Cache-Control": "private", **apis.deprecation_headers(m)}}


@router.get("/apis/{artifact_id}/monitor")
async def monitor(artifact_id: int):
    async with db.connection() as conn:
        a = await artifacts.get(conn, artifact_id)
        per_key = await db.fetch_all(conn, """select coalesce(l.key_id, 'bruger') as key_id, k.name, count(*) as calls,
                                              count(*) filter (where l.status >= 400) as errors, max(l.ts) as last_at
                                              from dev_api_log l left join dev_api_keys k on k.key_id = l.key_id
                                              where l.api = %s and l.ts >= date_trunc('day', now()) group by 1, 2 order by 3 desc""", (a["slug"],))
    return {**await mcp_studio.monitor(f"api:{a['slug']}"), "perKey": per_key}


# ── Keys (API-7) ────────────────────────────────────────────────────────────

async def _keys(conn, api_artifact_id: int | None = None) -> list[dict]:
    rows = await db.fetch_all(conn, """select k.id, k.key_id, k.name, k.kind, k.api_artifact_id, a.slug as api, k.scopes, k.ip_allow,
                                       k.expires_at, k.status, k.created_at, k.last_used_at, k.rotated_from, k.artifact_id
                                       from dev_api_keys k left join dev_artifacts a on a.id = k.api_artifact_id
                                       where %s::bigint is null or k.api_artifact_id = %s or k.api_artifact_id is null order by k.id desc""",
                              (api_artifact_id, api_artifact_id))
    now = datetime.now(timezone.utc)
    return [{**r, "ip_allow": [str(x) for x in r["ip_allow"]],
             "statusLabel": {"active": "Aktiv", "rotating": "Roteres", "revoked": "Tilbagekaldt"}[r["status"]] if r["expires_at"] > now else "Udløbet"}
            for r in rows]


@router.get("/api-keys")
async def list_keys(api: int | None = None):
    async with db.connection() as conn:
        return {"items": await _keys(conn, api)}


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="api_key", pattern="^(api_key|client_credentials)$")
    api_id: int | None = None
    scopes: list[str] = Field(min_length=1, max_length=50)
    expires_days: int = Field(default=365, ge=1, le=1095)
    ip_allow: list[str] = Field(default_factory=list, max_length=20)


@router.post("/api-keys", status_code=201)
async def create_key(body: KeyIn, p: Principal = Depends(require_admin_stepup)):
    """Only Udvikler-admin and Fuld (DEV_ADMIN_WRITES). The secret is shown once and stored only as a hash."""
    try:
        nets = [str(ipaddress.ip_network(x, strict=False)) for x in body.ip_allow]
    except ValueError:
        raise HTTPException(422, "IP-begrænsning skal være adresser eller net som 10.0.0.0/24")
    async with db.connection() as conn:
        if body.api_id:
            api_a = await artifacts.get(conn, body.api_id)
            if api_a["type"] != "api":
                raise HTTPException(404, "API'et findes ikke")
        key_id, secret = apis.new_key()
        art = await artifacts.create(conn, type_="api_key", slug=key_id.replace("_", "-"), name=body.name,
                                     definition={"name": body.name, "scopes": body.scopes, "api": body.api_id, "ip_allow": nets,
                                                 "expires_days": body.expires_days}, actor_id=p.user_id, meta=p.meta)
        row = await db.fetch_one(conn, """insert into dev_api_keys (key_id, secret_hash, name, kind, api_artifact_id, artifact_id, scopes, ip_allow,
                                          expires_at, created_by) values (%s, %s, %s, %s, %s, %s, %s, %s::cidr[], now() + make_interval(days => %s), %s)
                                          returning id""",
                                 (key_id, apis._hash(secret), body.name, body.kind, body.api_id, art["id"], body.scopes, nets,
                                  body.expires_days, p.user_id))
        await audit(conn, "dev.api_key.created", actor_id=p.user_id, meta=p.meta, table="dev_api_keys", row_id=row["id"],
                    after={"key_id": key_id, "name": body.name, "kind": body.kind, "scopes": body.scopes, "api": body.api_id,
                           "expires_days": body.expires_days, "ip_allow": nets})
    token = f"{key_id}.{secret}"
    return {"id": row["id"], "keyId": key_id, "secret": token if body.kind == "api_key" else secret,
            "clientId": key_id if body.kind == "client_credentials" else None,
            "note": "Nøglen vises kun én gang. Gem den et sikkert sted."}


class RotateIn(BaseModel):
    overlap_days: int = Field(default=7, ge=0, le=90)


@router.post("/api-keys/{key_pk}/rotate")
async def rotate_key(key_pk: int, body: RotateIn, p: Principal = Depends(require_admin_stepup)):
    """A new key with the same settings; the old one keeps working for `overlap_days`, then it expires."""
    async with db.connection() as conn:
        old = await db.fetch_one(conn, "select * from dev_api_keys where id = %s for update", (key_pk,))
        if old is None or old["status"] == "revoked":
            raise HTTPException(404, "Nøglen findes ikke eller er tilbagekaldt")
        key_id, secret = apis.new_key()
        row = await db.fetch_one(conn, """insert into dev_api_keys (key_id, secret_hash, name, kind, api_artifact_id, artifact_id, scopes, ip_allow,
                                          expires_at, rotated_from, created_by) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                                 (key_id, apis._hash(secret), old["name"], old["kind"], old["api_artifact_id"], old["artifact_id"],
                                  old["scopes"], old["ip_allow"], old["expires_at"], old["id"], p.user_id))
        until = min(old["expires_at"], datetime.now(timezone.utc) + timedelta(days=body.overlap_days))
        await conn.execute("update dev_api_keys set status = 'rotating', expires_at = %s where id = %s", (until, key_pk))
        await audit(conn, "dev.api_key.rotated", actor_id=p.user_id, meta=p.meta, table="dev_api_keys", row_id=key_pk,
                    new_key=key_id, overlap_until=until.isoformat())
    return {"id": row["id"], "keyId": key_id, "secret": f"{key_id}.{secret}" if old["kind"] == "api_key" else secret,
            "oldExpiresAt": until}


@router.post("/api-keys/{key_pk}/revoke")
async def revoke_key(key_pk: int, p: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "update dev_api_keys set status = 'revoked', revoked_at = now() where id = %s and status <> 'revoked' returning key_id",
                                 (key_pk,))
        if row is None:
            raise HTTPException(404, "Nøglen findes ikke eller er allerede tilbagekaldt")
        await conn.execute("delete from dev_api_tokens where key_id = %s", (key_pk,))
        await audit(conn, "dev.api_key.revoked", actor_id=p.user_id, meta=p.meta, table="dev_api_keys", row_id=key_pk, key_id=row["key_id"])
    return {"ok": True}


# ── Export and import (F-22 … F-24) ─────────────────────────────────────────

from app.devkit import portability  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402


@router.get("/instance-keys")
async def instance_keys():
    async with db.connection() as conn:
        return {"keys": await portability.public_keys(conn), "url": f"{_origin()}/.well-known/gaer-instance-keys"}


@router.get("/instances")
async def known_instances():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, "select id, name, fingerprint, encrypt_key is not null as can_encrypt, created_at from dev_known_instances order by name")
    return {"items": rows}


class InstanceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sign_key: str = Field(min_length=40, max_length=100)
    encrypt_key: str | None = Field(default=None, max_length=100)
    fingerprint: str = Field(min_length=10, max_length=80)


@router.post("/instances", status_code=201)
async def add_instance(body: InstanceIn, p: Principal = Depends(require_admin_stepup)):
    """The receiver's public keys. The fingerprint must match the one the other instance shows (confirmed by phone or
    in person, never only from the same mail as the key)."""
    if portability.fingerprint(body.sign_key) != body.fingerprint.strip().lower():
        raise HTTPException(422, "Fingeraftrykket passer ikke til nøglen. Bekræft det ad en anden kanal")
    async with db.connection() as conn:
        row = await db.fetch_one(conn, """insert into dev_known_instances (name, sign_key, encrypt_key, fingerprint, added_by)
                                          values (%s, %s, %s, %s, %s) returning id""",
                                 (body.name, body.sign_key, body.encrypt_key, body.fingerprint.strip().lower(), p.user_id))
        await audit(conn, "dev.instance.added", actor_id=p.user_id, meta=p.meta, table="dev_known_instances", row_id=row["id"],
                    name=body.name, fingerprint=body.fingerprint)
    return {"id": row["id"]}


@router.delete("/instances/{instance_id}")
async def remove_instance(instance_id: int, p: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "delete from dev_known_instances where id = %s returning name", (instance_id,))
        if row is None:
            raise HTTPException(404, "Instansen findes ikke")
        await audit(conn, "dev.instance.removed", actor_id=p.user_id, meta=p.meta, table="dev_known_instances", row_id=instance_id, name=row["name"])
    return {"ok": True}


class ExportIn(BaseModel):
    artifact_ids: list[int] = Field(min_length=1, max_length=200)
    include_catalog: bool = True
    secret_values: bool = False


@router.post("/export")
async def export(body: ExportIn, p: Principal = Depends(require_admin)):
    if body.secret_values:
        raise HTTPException(422, "Eksport af hemmelige værdier er ikke bygget. Filen indeholder kun referencer til hemmeligheder")
    async with db.connection() as conn:
        doc = await portability.export(conn, body.artifact_ids, include_catalog=body.include_catalog)
        await audit(conn, "dev.export", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=None,
                    artifacts=[f"{m['type']}/{m['slug']}" for m in doc["manifest"]], catalog=len(doc["catalog"]))
    return doc


class ImportIn(BaseModel):
    file: dict


@router.post("/import/check")
async def import_check(body: ImportIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        result = await portability.check(conn, body.file)
        await conn.execute("insert into dev_imports (file_hash, signer, summary, status, created_by) values (%s, %s, %s, %s, %s)",
                           (portability.hashlib.sha256(portability.canonical(body.file)).hexdigest(), result["signer"],
                            Jsonb({"actions": len(result["actions"]), "problems": result["problems"]}),
                            "checked" if result["ok"] else "blocked", p.user_id))
    return result


@router.post("/import")
async def run_import(body: ImportIn, p: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        result = await portability.run_import(conn, body.file, actor_id=p.user_id, meta=p.meta)
        await conn.execute("insert into dev_imports (file_hash, signer, summary, status, created_by) values (%s, %s, %s, 'imported', %s)",
                           (portability.hashlib.sha256(portability.canonical(body.file)).hexdigest(), result["signer"],
                            Jsonb({"imported": result["imported"]}), p.user_id))
        await audit(conn, "dev.import", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=None,
                    signer=result["signer"], artifacts=[f"{x['type']}/{x['slug']}" for x in result["imported"]])
    return result
