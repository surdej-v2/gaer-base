"""/admin/data → MCP-værktøjer: which endpoint offers which tool, and who may use it (app/mcp/toolmap.py).

- Code tools come from the code; here they can be put on or taken off the endpoints that have a handler, switched
  off, and classified white/green/yellow/red.
- Data tools are made from a dataset in the catalog. Their classification IS the dataset's (change it on the dataset),
  and fields are masked by their own classification.
CORE tools cannot be changed: without them nobody could ask for access. Every change is audited; anything that
changes who may use a tool needs a fresh second factor.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.logs import audit
from app.mcp import endpoints as endpoints_mod
from app.mcp import toolmap
from app.mcp.registry import ToolError
from app.sessions import Principal, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/data/tools", tags=["admin"], dependencies=[Depends(require_admin)])

NAME = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


async def _tool(conn, tool_id: int) -> dict:
    try:
        return await toolmap.get(conn, tool_id)
    except ToolError as e:
        raise HTTPException(404, str(e))


@router.get("")
async def tools():
    async with db.connection() as conn:
        rows = await toolmap.all_tools(conn)
        all_endpoints = await endpoints_mod.all_endpoints(conn)
        datasets = await db.fetch_all(conn, """select id, name, classification, personal_data, mcp_exposed from datasets
                                               order by name""")
    keys = [e["key"] for e in all_endpoints]
    return {"tools": [toolmap.out(t, keys) for t in rows], "datasets": datasets,
            "endpoints": [{"id": e["key"], "path": endpoints_mod.path_for(e["key"]), "name": e["name"],
                           "builtin": e["builtin"], "enabled": e["enabled"]} for e in all_endpoints],
            "classifications": [{"id": c, "label": toolmap.CLASS_LABELS[c]} for c in toolmap.CLASSIFICATIONS]}


async def _set_endpoints(conn, t: dict, endpoints: list[str]) -> None:
    possible = toolmap.possible_endpoints(t, await endpoints_mod.keys(conn))
    bad = [e for e in endpoints if e not in possible]
    if bad:
        raise HTTPException(422, f"{t['name']} kan ikke tilbydes på {', '.join(endpoints_mod.path_for(e) for e in bad)}")
    await conn.execute("delete from mcp_tool_endpoints where tool_id = %s", (t["id"],))
    for e in sorted(set(endpoints)):
        await conn.execute("insert into mcp_tool_endpoints (tool_id, endpoint) values (%s, %s)", (t["id"], e))


async def _sync_exposed(conn, dataset_id: int) -> None:
    await conn.execute("""update datasets set mcp_exposed = exists (select 1 from mcp_tools where dataset_id = %s and enabled),
                          updated_at = now() where id = %s""", (dataset_id, dataset_id))


class DataToolIn(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    dataset_id: int
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    endpoints: list[str] = Field(default_factory=lambda: ["app"], max_length=50)


@router.post("", status_code=201)
async def create_data_tool(body: DataToolIn, admin: Principal = Depends(require_admin_stepup)):
    if not NAME.match(body.name):
        raise HTTPException(422, "Navnet må kun have små bogstaver, tal og _ og skal starte med et bogstav")
    async with db.connection() as conn:
        await toolmap.sync(conn)
        if await db.fetch_one(conn, "select 1 from mcp_tools where name = %s", (body.name,)):
            raise HTTPException(409, f"Der findes allerede et værktøj med navnet {body.name}")
        if not await db.fetch_one(conn, "select 1 from datasets where id = %s", (body.dataset_id,)):
            raise HTTPException(404, "Datasættet findes ikke")
        row = await db.fetch_one(conn, """insert into mcp_tools (name, kind, dataset_id, title, description)
                                          values (%s, 'data', %s, %s, %s) returning id""",
                                 (body.name, body.dataset_id, body.title.strip(), body.description.strip()))
        t = await _tool(conn, row["id"])
        await _set_endpoints(conn, t, body.endpoints)
        await _sync_exposed(conn, body.dataset_id)
        t = await _tool(conn, row["id"])
        await audit(conn, "mcp_tool.created", actor_id=admin.user_id, meta=admin.meta, table="mcp_tools", row_id=t["id"],
                    after={"name": t["name"], "dataset_id": body.dataset_id, "endpoints": list(t["endpoints"])})
        keys = await endpoints_mod.keys(conn)
    return toolmap.out(t, keys)


class ToolPatch(BaseModel):
    classification: str | None = Field(default=None, pattern="^(white|green|yellow|red)$")
    endpoints: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None
    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


@router.patch("/{tool_id}")
async def update_tool(tool_id: int, body: ToolPatch, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        t = await _tool(conn, tool_id)
        keys = await endpoints_mod.keys(conn)
        before = toolmap.out(t, keys)
        if t["name"] in toolmap.CORE and (body.classification not in (None, "white") or body.enabled is False
                                          or body.endpoints is not None):
            raise HTTPException(409, f"{t['name']} er et kerneværktøj: det er altid åbent, så man kan bede om adgang")
        if t["kind"] == "operation" and any(v is not None for v in (body.classification, body.endpoints, body.title, body.description)):
            raise HTTPException(409, "Værktøjet er lavet i Datastudie. Niveau, beskrivelse og endpoint ændres der; her kan det kun slås fra")
        if body.classification is not None:
            if t["kind"] == "data":
                raise HTTPException(409, "Et dataværktøj følger datasættets klassifikation. Ret den på datasættet")
            await conn.execute("update mcp_tools set classification = %s where id = %s", (body.classification, tool_id))
        if body.enabled is not None:
            await conn.execute("update mcp_tools set enabled = %s where id = %s", (body.enabled, tool_id))
        if body.title is not None:
            await conn.execute("update mcp_tools set title = %s where id = %s", (body.title.strip(), tool_id))
        if body.description is not None:
            await conn.execute("update mcp_tools set description = %s where id = %s", (body.description.strip(), tool_id))
        if body.endpoints is not None:
            await _set_endpoints(conn, t, body.endpoints)
        await conn.execute("update mcp_tools set updated_at = now() where id = %s", (tool_id,))
        if t["kind"] == "data":
            await _sync_exposed(conn, t["dataset_id"])
        after = toolmap.out(await _tool(conn, tool_id), keys)
        keys = ("classification", "endpoints", "enabled", "title", "description")
        await audit(conn, "mcp_tool.updated", actor_id=admin.user_id, meta=admin.meta, table="mcp_tools", row_id=tool_id,
                    before={k: before[k] for k in keys if before[k] != after[k]},
                    after={k: after[k] for k in keys if before[k] != after[k]}, name=t["name"])
    return after


@router.delete("/{tool_id}")
async def delete_tool(tool_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        t = await _tool(conn, tool_id)
        if t["kind"] != "data":
            raise HTTPException(409, "Kodeværktøjer kan ikke slettes, kun slås fra eller tages af et endpoint")
        await conn.execute("delete from mcp_tools where id = %s", (tool_id,))
        await _sync_exposed(conn, t["dataset_id"])
        await audit(conn, "mcp_tool.deleted", actor_id=admin.user_id, meta=admin.meta, table="mcp_tools", row_id=tool_id,
                    before={"name": t["name"], "dataset_id": t["dataset_id"]})
    return {"ok": True}
