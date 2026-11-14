"""/api/admin/studio: the developer portal on /developer (dashboard, API Studio, MCP Studio). Admin section
"/developer" (roles Data and Fuld, app/admin_roles.py). GETs are logged as admin.read by require_admin.

- overview: the numbers behind the dashboard and the Studio roll-up (API sources, catalog, tools, endpoints, calls).
- mcp-endpoints: the dynamic MCP endpoints /mcp/<key> (app/mcp/endpoints.py). Creating, changing and deleting one,
  and choosing its tools, needs a fresh second factor — it changes what AI clients can reach. Every change is audited.
- invoke: runs a tool on an endpoint as the admin, exactly as over MCP (registry.invoke, Caller via="studio").
API sources themselves are managed through /api/admin/data/sources (routes/data.py), which API Studio calls directly.
"""

import re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.logs import audit
from app.mcp import endpoints as endpoints_mod
from app.mcp import oauth, toolmap
from app.mcp.registry import Caller, invoke, tool_list
from app.mcp.transport import enabled as mcp_enabled
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/studio", tags=["admin"], dependencies=[Depends(require_admin)])

KEY = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
# Core tools every endpoint offers from the start: without them nobody could ask for access (toolmap.CORE).
STARTER_TOOLS = ("whoami", "access_list", "access_request", "confirm_draft", "cancel_draft")


async def _endpoint(conn, key: str) -> dict:
    row = await endpoints_mod.get(conn, key) if KEY.match(key) else None
    if row is None:
        raise HTTPException(404, "Endpointet findes ikke")
    return row


def _dynamic(row: dict) -> dict:
    if row["builtin"]:
        raise HTTPException(409, f"{endpoints_mod.path_for(row['key'])} er indbygget og styres fra /admin/data/tools")
    if row.get("artifact_id"):
        raise HTTPException(409, f"{endpoints_mod.path_for(row['key'])} er lavet i Datastudie og ændres der (versioner og godkendelse)")
    return row


async def _endpoint_stats(conn, days: int = 7) -> dict[str, dict]:
    rows = await db.fetch_all(conn, """
        select server, count(*) filter (where method = 'tools/call') as calls,
               count(*) filter (where status in ('error', 'forbidden', 'auth_failed')) as errors,
               count(distinct user_id) as callers, max(ts) as last_at
        from mcp_log where ts > now() - make_interval(days => %s) group by server""", (days,))
    return {r["server"]: r for r in rows}


async def _tool_counts(conn) -> dict[str, int]:
    rows = await db.fetch_all(conn, """select e.endpoint, count(*) as n from mcp_tool_endpoints e
                                       join mcp_tools t on t.id = e.tool_id where t.enabled group by e.endpoint""")
    return {r["endpoint"]: r["n"] for r in rows}


def _endpoint_out(row: dict, stats: dict, tools: int) -> dict:
    s = stats.get(row["key"])
    return {**endpoints_mod.out(row), "url": oauth.resource_url(row["key"]),
            "live": row["enabled"] and mcp_enabled(row["key"]), "tools": tools,
            "calls7d": s["calls"] if s else 0, "errors7d": s["errors"] if s else 0, "callers7d": s["callers"] if s else 0,
            "lastAt": s["last_at"] if s else None}


# ── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/overview")
async def overview():
    async with db.connection() as conn:
        await toolmap.sync(conn)    # the Code tools' rows and the built-in endpoints exist from here on
        sources = await db.fetch_all(conn, """
            select s.id, s.key, s.name, s.hosts, s.updated_at,
                   (select count(*) from datasets d where d.source_id = s.id) as datasets
            from data_sources s order by s.updated_at desc""")
        api = await db.fetch_all(conn, """
            select split_part(ref, ':', 2) as source_key, count(*) as calls,
                   count(*) filter (where coalesce((data->>'status')::int, 0) not between 1 and 399) as errors,
                   max(ts) as last_at
            from events where kind = 'api.call' and ref like %s and ts > now() - interval '7 days'
            group by 1""", ("source:%",))
        catalog = await db.fetch_one(conn, """
            select count(*) as datasets, count(*) filter (where source_id is not null) as from_api,
                   count(*) filter (where mcp_exposed) as mcp_exposed from datasets""")
        tools = await db.fetch_one(conn, """select count(*) as total, count(*) filter (where kind = 'data') as data,
                                            count(*) filter (where kind = 'code') as code,
                                            count(*) filter (where not enabled) as disabled from mcp_tools""")
        all_endpoints = await endpoints_mod.all_endpoints(conn)
        stats, counts = await _endpoint_stats(conn), await _tool_counts(conn)
        clients = await db.fetch_one(conn, """
            select count(distinct s.mcp_client_id) as n from sessions s
            where s.mcp_client_id is not null and s.revoked_at is null and s.expires_at > now()""")
        recent = await db.fetch_all(conn, """
            select l.ts, l.server, l.method, l.tool, l.status, l.duration_ms, u.display_name, u.email
            from mcp_log l left join users u on u.id = l.user_id
            where l.method = 'tools/call' order by l.ts desc limit 12""")
    by_key = {r["source_key"]: r for r in api}
    return {
        "sources": {"count": len(sources),
                    "calls7d": sum(r["calls"] for r in api), "errors7d": sum(r["errors"] for r in api),
                    "items": [{"id": s["id"], "key": s["key"], "name": s["name"], "host": s["hosts"][0] if s["hosts"] else None,
                               "datasets": s["datasets"], "updatedAt": s["updated_at"],
                               "calls7d": by_key[s["key"]]["calls"] if s["key"] in by_key else 0,
                               "errors7d": by_key[s["key"]]["errors"] if s["key"] in by_key else 0,
                               "lastAt": by_key[s["key"]]["last_at"] if s["key"] in by_key else None} for s in sources]},
        "catalog": {"datasets": catalog["datasets"], "fromApi": catalog["from_api"], "mcpExposed": catalog["mcp_exposed"]},
        "tools": {"total": tools["total"], "data": tools["data"], "code": tools["code"], "disabled": tools["disabled"]},
        "endpoints": {"count": len(all_endpoints), "dynamic": sum(1 for e in all_endpoints if not e["builtin"]),
                      "calls7d": sum(s["calls"] for s in stats.values()),
                      "errors7d": sum(s["errors"] for s in stats.values()), "clients": clients["n"],
                      "items": [_endpoint_out(e, stats, counts.get(e["key"], 0)) for e in all_endpoints]},
        "recent": [{"ts": r["ts"], "endpoint": r["server"], "path": endpoints_mod.path_for(r["server"]), "tool": r["tool"],
                    "status": r["status"], "ms": r["duration_ms"],
                    "who": r["display_name"] or (r["email"] or "").split("@")[0] or None} for r in recent],
        "origin": oauth.origin(),
    }


# ── MCP endpoints ───────────────────────────────────────────────────────────

@router.get("/mcp-endpoints")
async def list_endpoints():
    async with db.connection() as conn:
        rows = await endpoints_mod.all_endpoints(conn)
        stats, counts = await _endpoint_stats(conn), await _tool_counts(conn)
    return {"endpoints": [_endpoint_out(e, stats, counts.get(e["key"], 0)) for e in rows],
            "audiences": [{"id": k, "label": v} for k, v in endpoints_mod.AUDIENCES.items()],
            "reserved": sorted(endpoints_mod.RESERVED), "origin": oauth.origin()}


class EndpointIn(BaseModel):
    key: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    instructions: str = Field(default="", max_length=4000)
    audience: str = Field(default="all", pattern="^(all|internal|admin)$")
    enabled: bool = True


async def _attach(conn, key: str, names: tuple[str, ...]) -> list[str]:
    """Puts the named Code tools on the endpoint (those with an app handler). Returns what was attached."""
    await toolmap.sync(conn)
    attached = []
    for name in names:
        t = await toolmap.by_name(conn, name)
        if t is None or not toolmap.has_handler(key, name):
            continue
        await conn.execute("insert into mcp_tool_endpoints (tool_id, endpoint) values (%s, %s) on conflict do nothing",
                           (t["id"], key))
        attached.append(name)
    return attached


@router.post("/mcp-endpoints", status_code=201)
async def create_endpoint(body: EndpointIn, admin: Principal = Depends(require_admin_stepup)):
    key = body.key.strip().lower()
    if not KEY.match(key):
        raise HTTPException(422, "Nøglen må kun have små bogstaver, tal og bindestreg og skal starte med et bogstav")
    if key in endpoints_mod.RESERVED:
        raise HTTPException(422, f"/mcp/{key} er reserveret")
    async with db.connection() as conn:
        if await endpoints_mod.get(conn, key):
            raise HTTPException(409, f"Der findes allerede et endpoint på /mcp/{key}")
        await conn.execute("""
            insert into mcp_endpoints (key, name, description, instructions, audience, enabled, created_by)
            values (%s, %s, %s, %s, %s, %s, %s)""",
            (key, body.name.strip(), body.description.strip(), body.instructions.strip(), body.audience, body.enabled,
             admin.user_id))
        tools = await _attach(conn, key, STARTER_TOOLS)
        row = await _endpoint(conn, key)
        await audit(conn, "mcp_endpoint.created", actor_id=admin.user_id, meta=admin.meta, table="mcp_endpoints",
                    row_id=key, after={"key": key, "name": row["name"], "audience": row["audience"],
                                       "enabled": row["enabled"], "tools": tools})
        stats, counts = await _endpoint_stats(conn), await _tool_counts(conn)
    return _endpoint_out(row, stats, counts.get(key, 0))


@router.get("/mcp-endpoints/{key}")
async def endpoint_detail(key: str):
    """The endpoint, the tools it offers, every tool it could offer, and how a client connects."""
    async with db.connection() as conn:
        row = await _endpoint(conn, key)
        stats, counts = await _endpoint_stats(conn), await _tool_counts(conn)
        keys = await endpoints_mod.keys(conn)
        all_tools = [toolmap.out(t, keys) for t in await toolmap.all_tools(conn)]
        datasets = await db.fetch_all(conn, """select d.id, d.name, d.key, d.classification, d.personal_data, s.name as source
                                               from datasets d left join data_sources s on s.id = d.source_id
                                               order by d.name""")
        clients = await db.fetch_all(conn, """
            select c.name, count(s.id) as sessions, max(s.last_seen_at) as last_seen_at
            from sessions s join mcp_clients c on c.id = s.mcp_client_id
            where s.mcp_scope = %s and s.revoked_at is null and s.expires_at > now()
            group by c.id, c.name order by max(s.last_seen_at) desc nulls last limit 20""",
            (endpoints_mod.scope_for(key),))
    offered = await tool_list(key)
    available = [t for t in all_tools if key in t["possibleEndpoints"]]
    return {"endpoint": _endpoint_out(row, stats, counts.get(key, 0)),
            "tools": offered, "available": available, "datasets": datasets, "clients": clients,
            "core": sorted(toolmap.CORE), "starter": list(STARTER_TOOLS),
            "connect": {"url": oauth.resource_url(key), "scope": endpoints_mod.scope_for(key),
                        "metadata": f"{oauth.origin()}/.well-known/oauth-protected-resource{endpoints_mod.path_for(key)}",
                        "authorization": f"{oauth.origin()}/mcp/authorize", "token": f"{oauth.origin()}/mcp/token",
                        "register": f"{oauth.origin()}/mcp/register"}}


class EndpointPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None, max_length=4000)
    audience: str | None = Field(default=None, pattern="^(all|internal|admin)$")
    enabled: bool | None = None


@router.patch("/mcp-endpoints/{key}")
async def update_endpoint(key: str, body: EndpointPatch, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = _dynamic(await _endpoint(conn, key))
        fields = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.model_dump(exclude_none=True).items()}
        if not fields:
            raise HTTPException(422, "Intet at ændre")
        sets = ", ".join(f"{k} = %s" for k in fields)
        await conn.execute(f"update mcp_endpoints set {sets}, updated_at = now() where key = %s", (*fields.values(), key))
        after_row = await _endpoint(conn, key)
        before = {k: row[k] for k in fields if row[k] != after_row[k]}
        after = {k: after_row[k] for k in fields if row[k] != after_row[k]}
        await audit(conn, "mcp_endpoint.updated", actor_id=admin.user_id, meta=admin.meta, table="mcp_endpoints",
                    row_id=key, before=before, after=after)
        stats, counts = await _endpoint_stats(conn), await _tool_counts(conn)
    return _endpoint_out(after_row, stats, counts.get(key, 0))


@router.delete("/mcp-endpoints/{key}")
async def delete_endpoint(key: str, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        row = _dynamic(await _endpoint(conn, key))
        tools = [r["name"] for r in await db.fetch_all(conn, """select t.name from mcp_tool_endpoints e
                                                                 join mcp_tools t on t.id = e.tool_id where e.endpoint = %s
                                                                 order by t.name""", (key,))]
        await conn.execute("delete from mcp_endpoints where key = %s", (key,))
        await audit(conn, "mcp_endpoint.deleted", actor_id=admin.user_id, meta=admin.meta, table="mcp_endpoints",
                    row_id=key, before={"key": key, "name": row["name"], "audience": row["audience"], "tools": tools})
    return {"ok": True}


class ToolsIn(BaseModel):
    tool_ids: list[int] = Field(max_length=200)


@router.put("/mcp-endpoints/{key}/tools")
async def set_tools(key: str, body: ToolsIn, admin: Principal = Depends(require_admin_stepup)):
    """Which tools the endpoint offers. Core tools stay; a Code tool needs an app handler; Data tools go anywhere."""
    async with db.connection() as conn:
        _dynamic(await _endpoint(conn, key))
        keys = await endpoints_mod.keys(conn)
        before = [r["name"] for r in await db.fetch_all(conn, """select t.name from mcp_tool_endpoints e
                                                                  join mcp_tools t on t.id = e.tool_id where e.endpoint = %s
                                                                  order by t.name""", (key,))]
        wanted = []
        for tool_id in sorted(set(body.tool_ids)):
            t = await db.fetch_one(conn, f"select {toolmap.TOOL_COLUMNS} from {toolmap.TOOL_FROM} where t.id = %s", (tool_id,))
            if t is None:
                raise HTTPException(404, f"Værktøj {tool_id} findes ikke")
            if key not in toolmap.possible_endpoints(t, keys):
                raise HTTPException(422, f"{t['name']} kan ikke tilbydes på {endpoints_mod.path_for(key)}")
            wanted.append(t)
        await conn.execute("delete from mcp_tool_endpoints where endpoint = %s", (key,))
        for t in wanted:
            await conn.execute("insert into mcp_tool_endpoints (tool_id, endpoint) values (%s, %s)", (t["id"], key))
        await _attach(conn, key, STARTER_TOOLS)
        for t in wanted:
            if t["kind"] == "data":
                await conn.execute("""update datasets set mcp_exposed = true, updated_at = now()
                                      where id = %s and not mcp_exposed""", (t["dataset_id"],))
        after = [r["name"] for r in await db.fetch_all(conn, """select t.name from mcp_tool_endpoints e
                                                                 join mcp_tools t on t.id = e.tool_id where e.endpoint = %s
                                                                 order by t.name""", (key,))]
        if before != after:
            await audit(conn, "mcp_endpoint.tools", actor_id=admin.user_id, meta=admin.meta, table="mcp_endpoints",
                        row_id=key, before={"tools": before}, after={"tools": after})
    return {"tools": await tool_list(key)}


class InvokeIn(BaseModel):
    args: dict = Field(default_factory=dict)


@router.post("/mcp-endpoints/{key}/tools/{name}/invoke")
async def invoke_tool(key: str, name: str, body: InvokeIn, admin: Principal = Depends(require_admin)):
    """Runs a tool on the endpoint as the admin — the same path as over MCP, so the call lands in mcp_log."""
    async with db.connection() as conn:
        await _endpoint(conn, key)
    if name not in {t["name"] for t in await tool_list(key)}:
        raise HTTPException(404, "Værktøjet er ikke på endpointet")
    started = time.monotonic()
    result, is_error = await invoke(key, name, body.args, Caller(server=key, principal=admin, client_id=None, via="studio"))
    return {"result": result, "isError": is_error, "ms": int((time.monotonic() - started) * 1000)}


@router.get("/mcp-endpoints/{key}/log")
async def endpoint_log(key: str, limit: int = 50):
    async with db.connection() as conn:
        await _endpoint(conn, key)
        rows = await db.fetch_all(conn, """
            select l.ts, l.method, l.tool, l.status, l.duration_ms, l.client_id, u.display_name, u.email
            from mcp_log l left join users u on u.id = l.user_id
            where l.server = %s order by l.ts desc limit %s""", (key, max(1, min(limit, 200))))
    return {"items": [{"ts": r["ts"], "method": r["method"], "tool": r["tool"], "status": r["status"], "ms": r["duration_ms"],
                       "client": r["client_id"], "who": r["display_name"] or (r["email"] or "").split("@")[0] or None}
                      for r in rows]}
