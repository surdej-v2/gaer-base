"""MCP endpoints made in Datastudie (DS-3 … DS-9, MCP-1 … MCP-6): an artifact of type `mcp_endpoint` whose slug is the
endpoint key, served on /mcp/<slug> by the existing dynamic endpoints (app/mcp/endpoints.py, transport.py).

    {"name": "Lejedata", "description": "…", "instructions": "…", "audience": "internal",
     "guide": {…},                                    # the interview, saved as it goes (DS-3 … DS-7)
     "tools": [{"name": "soeg_lejemaal", "operation": 41, "description": "Søg lejemål på adresse eller postnr.",
                "level": "white", "examples": [{"args": {"postnr": "8000"}}]}]}

Publishing a version writes the endpoint row and one `mcp_tools` row (kind `operation`) per tool, so clients only ever
see the approved version. The tool's level is its classification (MCP-5): white needs no approval, yellow a grant the
user approves, red the user and an approver in /admin (the existing access requests); external users only see white
tools. A tool can be switched off without being deleted (MCP-3).
"""

import re

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import db
from app.devkit import artifacts
from app.mcp import endpoints as endpoints_mod

TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
LEVELS = ("white", "green", "yellow", "red")
STARTER = ("whoami", "access_list", "access_request", "confirm_draft", "cancel_draft")


async def prepare(conn, artifact: dict | None, definition: dict) -> dict:
    """Validates the tools and records each tool's operation contract next to it (for the endpoint's contract)."""
    tools, seen = [], set()
    for t in definition.get("tools") or []:
        name = str(t.get("name") or "")
        if not TOOL_NAME.match(name):
            raise HTTPException(422, f"Toolnavnet {name or '(tomt)'} skal have små bogstaver, tal og _ (2-48 tegn)")
        if name in seen:
            raise HTTPException(422, f"{name} findes to gange")
        seen.add(name)
        op = await db.fetch_one(conn, f"select {artifacts.COLUMNS} from dev_artifacts a where a.id = %s and a.type = 'operation'",
                                (int(t.get("operation") or 0),))
        if op is None:
            raise HTTPException(422, f"{name}: operationen findes ikke")
        v = await artifacts.current(conn, op) or await artifacts.open_version(conn, op["id"])
        if v is None:
            raise HTTPException(422, f"{name}: operationen har ingen version")
        taken = await db.fetch_one(conn, """select t.id from mcp_tools t where t.name = %s and
                                            (t.kind <> 'operation' or exists (select 1 from mcp_tool_endpoints e where e.tool_id = t.id
                                             and e.endpoint <> %s))""", (name, artifact["slug"] if artifact else ""))
        if taken:
            raise HTTPException(409, f"Toolnavnet {name} bruges allerede af et andet endpoint eller værktøj")
        level = t.get("level") or "white"
        if level not in LEVELS:
            raise HTTPException(422, f"{name}: niveau skal være hvid, grøn, gul eller rød")
        tools.append({"name": name, "operation": op["id"], "operation_slug": op["slug"],
                      "description": str(t.get("description") or v["definition"].get("description") or "")[:1000],
                      "level": level, "enabled": t.get("enabled", True) is not False,
                      "examples": list(t.get("examples") or v["definition"].get("examples") or [])[:10],
                      "_contract": v["contract"]})
    audience = definition.get("audience") or "internal"
    if audience not in endpoints_mod.AUDIENCES:
        raise HTTPException(422, "Målgruppen skal være all, internal eller admin")
    return {"name": str(definition.get("name") or "")[:80], "description": str(definition.get("description") or "")[:1000],
            "instructions": str(definition.get("instructions") or "")[:4000], "audience": audience,
            "guide": definition.get("guide") or {}, "tools": tools}


def contract(definition: dict) -> dict:
    fields, params = {}, {}
    for t in definition.get("tools") or []:
        c = t.get("_contract") or {}
        for k, v in (c.get("fields") or {}).items():
            fields[f"{t['name']}.{k}"] = v
        for k, v in (c.get("params") or {}).items():
            params[f"{t['name']}.{k}"] = v
    return {"items": [t["name"] for t in definition.get("tools") or []], "fields": fields, "params": params}


async def dependencies(conn, definition: dict) -> list[tuple[int, str]]:
    return [(t["operation"], t["name"]) for t in definition.get("tools") or []]


async def on_publish(conn, artifact: dict, v: dict, prev: dict | None) -> None:
    d = v["definition"]
    key = artifact["slug"]
    await endpoints_mod.ensure_builtin(conn)
    await conn.execute("""insert into mcp_endpoints (key, name, description, instructions, audience, enabled, created_by, artifact_id)
                          values (%s, %s, %s, %s, %s, %s, %s, %s)
                          on conflict (key) do update set name = excluded.name, description = excluded.description,
                          instructions = excluded.instructions, audience = excluded.audience, artifact_id = excluded.artifact_id,
                          updated_at = now()""",
                       (key, d["name"] or artifact["name"], d["description"], d["instructions"], d["audience"],
                        artifact["status"] != "disabled", v["created_by"], artifact["id"]))
    wanted = {t["name"]: t for t in d.get("tools") or []}
    old = await db.fetch_all(conn, """select t.id, t.name from mcp_tools t join mcp_tool_endpoints e on e.tool_id = t.id
                                      where e.endpoint = %s and t.kind = 'operation'""", (key,))
    for o in old:
        if o["name"] not in wanted:
            await conn.execute("delete from mcp_tools where id = %s", (o["id"],))
    for t in wanted.values():
        row = await db.fetch_one(conn, """insert into mcp_tools (name, kind, artifact_id, title, description, classification, enabled, examples)
                                          values (%s, 'operation', %s, %s, %s, %s, %s, %s)
                                          on conflict (name) do update set artifact_id = excluded.artifact_id, title = excluded.title,
                                          description = excluded.description, classification = excluded.classification,
                                          enabled = excluded.enabled, examples = excluded.examples, updated_at = now()
                                          returning id""",
                                 (t["name"], t["operation"], t.get("operation_slug") or "", t["description"], t["level"],
                                  t["enabled"], Jsonb(t["examples"])))
        await conn.execute("insert into mcp_tool_endpoints (tool_id, endpoint) values (%s, %s) on conflict do nothing", (row["id"], key))
    from app.routes.studio import _attach
    await _attach(conn, key, STARTER)


artifacts.register("mcp_endpoint", artifacts.TypeSpec(
    contract=contract, dependencies=dependencies, on_publish=on_publish,
    other={"description": "Beskrivelse", "instructions": "Instruktioner", "audience": "Målgruppe", "name": "Navn", "guide": "Guide"}))


async def set_enabled(conn, artifact: dict, enabled: bool) -> None:
    await conn.execute("update mcp_endpoints set enabled = %s, updated_at = now() where key = %s and artifact_id = %s",
                       (enabled, artifact["slug"], artifact["id"]))
