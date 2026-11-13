"""Which endpoint offers which tool, and who may call it — kept in the database and governed from /admin/data.

- A **Code tool** is a handler in code (registry.HANDLERS, named in catalog.json). Its row is created from the
  catalog the first time it is seen, on the endpoints it has a handler for, classified white (open to everyone who
  may use that endpoint). An admin can take it off an endpoint, switch it off or raise its classification.
- A **Data tool** reads one dataset of the catalog in /admin/data (app/mcp/data_tools.py). It has no classification
  of its own: it follows the dataset's, and its fields are masked by their own classification.

One tool can be on several endpoints (`mcp_tool_endpoints`); a Code tool only on endpoints with a handler. The
endpoints themselves are rows in mcp_endpoints (app/mcp/endpoints.py): the built-in /mcp and /mcp/admin and the ones
made in /developer/studio/mcp, which run the app's handlers.

Classification → who may call the tool (on top of who may use the endpoint at all):

    white   everyone
    green   internal users
    yellow  internal users with a grant the user approved (access_request)
    red     internal users with a grant the user and an approver approved

A grant also unmasks a Data tool's fields: yellow fields with any grant, red fields with a confidential grant.
CORE tools (whoami, drafts, access_*) are always on and always white: without them nobody could ask for access.
"""

from app import db
from app.mcp import endpoints as endpoints_mod
from app.mcp.registry import CATALOG, HANDLERS, ToolError

ENDPOINTS = endpoints_mod.BUILTIN
ENDPOINT_LABELS = {"app": "/mcp", "admin": "/mcp/admin"}
CLASSIFICATIONS = ("white", "green", "yellow", "red")
CLASS_LABELS = {"white": "Hvid · alle", "green": "Grøn · interne", "yellow": "Gul · interne med godkendelse",
                "red": "Rød · interne med godkendelse af bruger og godkender"}
CORE = {"whoami", "confirm_draft", "cancel_draft", "list_drafts", "access_list", "access_request"}
LEVEL_RANK = {None: 0, "own": 0, "internal": 1, "confidential": 2}
DATA_PARAMS = [
    {"name": "field", "type": "string", "desc": "filter: field name (see columns)", "optional": True},
    {"name": "value", "type": "string", "desc": "filter: the field must equal this value", "optional": True},
    {"name": "limit", "type": "int", "desc": "max 200 (default 50)", "optional": True},
    {"name": "offset", "type": "int", "desc": "skip this many rows", "optional": True},
]

TOOL_COLUMNS = """t.id, t.name, t.kind, t.dataset_id, t.title, t.description, t.enabled, t.classification as own_classification,
                  coalesce(t.classification, d.classification) as classification, t.updated_at,
                  d.name as dataset_name, d.description as dataset_description, d.personal_data,
                  (select max(case f.classification when 'red' then 2 when 'yellow' then 1 else 0 end)
                     from dataset_fields f where f.dataset_id = t.dataset_id) as field_rank,
                  array(select e.endpoint from mcp_tool_endpoints e where e.tool_id = t.id order by e.endpoint) as endpoints,
                  t.artifact_id, t.examples,
                  (select v.definition from dev_artifacts a join dev_artifact_versions v on v.id = a.current_version_id
                    where a.id = t.artifact_id) as op_definition"""
TOOL_FROM = "mcp_tools t left join datasets d on d.id = t.dataset_id"


def code_names() -> dict[str, list[str]]:
    """Code tool name → the built-in endpoints that have a handler for it (from the catalog)."""
    out: dict[str, list[str]] = {}
    for t in CATALOG["tools"]:
        out.setdefault(t["name"], []).append(t["server"])
    return out


def possible_endpoints(t: dict, keys: list[str]) -> list[str]:
    """Where this tool can be offered, given every endpoint key: a Data tool anywhere, a Code tool on the built-in
    endpoints that have its handler and — when the app has one — on every dynamic endpoint (they run the app's)."""
    if t["kind"] != "code":
        return list(keys)       # Data and operation tools can be offered anywhere
    servers = code_names().get(t["name"], [])
    return [k for k in keys if (k in ENDPOINTS and k in servers) or (k not in ENDPOINTS and "app" in servers)]


def has_handler(endpoint: str, name: str) -> bool:
    return (endpoints_mod.handler_server(endpoint), name) in HANDLERS


def catalog_spec(endpoint: str, name: str) -> dict | None:
    return next((t for t in CATALOG["tools"] if t["server"] == endpoint and t["name"] == name), None)


async def sync(conn) -> None:
    """Adds rows for Code tools that have none yet (a new handler, or a fresh database), and the built-in endpoints."""
    names = code_names()
    have = await db.fetch_one(conn, "select count(*) as n from mcp_tools where kind = 'code'")
    if have["n"] >= len(names):
        return
    await endpoints_mod.ensure_builtin(conn)
    for name, servers in names.items():
        spec = catalog_spec(servers[0], name)
        row = await db.fetch_one(conn, """
            insert into mcp_tools (name, kind, description, classification) values (%s, 'code', %s, 'white')
            on conflict (name) do nothing returning id""", (name, spec["description"]))
        if row:
            for s in servers:
                await conn.execute("insert into mcp_tool_endpoints (tool_id, endpoint) values (%s, %s) on conflict do nothing",
                                   (row["id"], s))


def needed_level(classification: str) -> str | None:
    return {"yellow": "internal", "red": "confidential"}.get(classification)


def request_level(tool: dict) -> str | None:
    """What a grant for this tool would be: the tool's own need, or for a Data tool what unmasks its fields."""
    level = needed_level(tool["classification"])
    if tool["kind"] == "data":
        fields = {2: "confidential", 1: "internal"}.get(tool["field_rank"] or 0)
        if LEVEL_RANK[fields] > LEVEL_RANK[level]:
            level = fields
    return level


def out(t: dict, keys: list[str] | None = None) -> dict:
    """`keys`: every endpoint key (endpoints.keys); without it only the built-in endpoints are considered."""
    implemented = possible_endpoints(t, list(keys) if keys is not None else list(ENDPOINTS))
    return {"id": t["id"], "name": t["name"], "kind": t["kind"], "title": t["title"] or t.get("dataset_name") or "",
            "description": t["description"], "classification": t["classification"],
            "classificationLabel": CLASS_LABELS[t["classification"]], "ownClassification": t["own_classification"],
            "datasetId": t["dataset_id"], "datasetName": t["dataset_name"], "enabled": t["enabled"],
            "endpoints": list(t["endpoints"]), "possibleEndpoints": implemented, "core": t["name"] in CORE,
            "requestLevel": request_level(t), "updatedAt": t["updated_at"], "artifactId": t.get("artifact_id")}


async def get(conn, tool_id: int) -> dict:
    t = await db.fetch_one(conn, f"select {TOOL_COLUMNS} from {TOOL_FROM} where t.id = %s", (tool_id,))
    if t is None:
        raise ToolError("Værktøjet findes ikke")
    return t


async def all_tools(conn) -> list[dict]:
    await sync(conn)
    return await db.fetch_all(conn, f"select {TOOL_COLUMNS} from {TOOL_FROM} order by t.kind, t.name")


async def for_endpoint(conn, endpoint: str) -> list[dict]:
    await sync(conn)
    return await db.fetch_all(conn, f"""select {TOOL_COLUMNS} from {TOOL_FROM}
                                        join mcp_tool_endpoints e on e.tool_id = t.id and e.endpoint = %s
                                        where t.enabled order by t.kind, t.name""", (endpoint,))


async def resolve(conn, endpoint: str, name: str) -> dict | None:
    await sync(conn)
    t = await db.fetch_one(conn, f"""select {TOOL_COLUMNS} from {TOOL_FROM}
                                     join mcp_tool_endpoints e on e.tool_id = t.id and e.endpoint = %s
                                     where t.name = %s and t.enabled""", (endpoint, name))
    if t is None or (t["kind"] == "code" and not has_handler(endpoint, name)):
        return None
    return t


async def by_name(conn, name: str) -> dict | None:
    await sync(conn)
    return await db.fetch_one(conn, f"select {TOOL_COLUMNS} from {TOOL_FROM} where t.name = %s", (name,))


def spec_for(t: dict, endpoint: str) -> dict:
    """The tool in catalog form (name, mode, description, params) for tools/list, the hub and argument checks."""
    if t["kind"] == "code":
        spec = dict(catalog_spec(endpoints_mod.handler_server(endpoint), t["name"]))
        if t["description"] and t["description"] != spec["description"]:
            spec["description"] = t["description"]
    elif t["kind"] == "operation":
        from app.devkit.operations import param_specs
        definition = t.get("op_definition") or {}
        spec = {"server": endpoint, "domain": "operation", "name": t["name"], "mode": "read",
                "description": t["description"] or definition.get("description") or t["name"], "params": param_specs(definition),
                "link": None, "examples": t.get("examples") or []}
    else:
        desc = t["description"] or t["dataset_description"] or ""
        spec = {"server": endpoint, "domain": "data", "name": t["name"], "mode": "read",
                "description": f"Data from the catalog: {t['title'] or t['dataset_name']}. {desc}".strip(),
                "params": DATA_PARAMS, "link": None}
    return {**spec, "kind": t["kind"], "classification": t["classification"], "toolId": t["id"]}


async def decide(conn, t: dict, user_id: int, session_id: int) -> dict:
    """May this caller use the tool now? {allowed, message, grant, requestLevel}."""
    from app import access

    if t["name"] in CORE:
        return {"allowed": True, "message": None, "grant": None, "requestLevel": None}
    user = await db.fetch_one(conn, "select audience from users where id = %s", (user_id,))
    internal = user is not None and user["audience"] == "internal"
    grant = await access.tool_grant(conn, user_id, session_id, t["id"]) if internal else None
    need = needed_level(t["classification"])
    hint = (f"Bed om adgang med access_request(target=\"{t['name']}\", reason=…). Brugeren godkender"
            + (", og derefter en godkender i /admin." if request_level(t) == "confidential" else "."))
    if t["classification"] == "white":
        allowed, message = True, None
    elif not internal:
        allowed, message = False, "Værktøjet er kun for interne brugere"
    elif LEVEL_RANK[grant["level"] if grant else None] >= LEVEL_RANK[need]:
        allowed, message = True, None
    else:
        allowed, message = False, "Værktøjet kræver adgang. " + hint
    return {"allowed": allowed, "message": message, "grant": grant,
            "requestLevel": request_level(t) if internal else None}
