"""Tool registry for both MCP servers. catalog.json (a copy of specs/mcp/catalog.json) is the source of truth
for names, modes, parameters and links; every catalog tool must have exactly one handler and vice versa.

A read tool:   @tool("app", "clips_list")  async def run(args, caller) -> ToolOutput
A draft tool:  @draft("admin", "user_block") async def plan(args, caller, conn) -> DraftPlan
A write tool:  @tool("app", "clip_create") — writes own data directly, with audit() in the same transaction.

invoke() is the one entry point, used by the MCP transport and by the /admin hub; it logs every call to mcp_log.
"""

import json
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import db
from app.sessions import Principal

CATALOG: dict = json.loads((pathlib.Path(__file__).parent / "catalog.json").read_text())
SERVERS = ("app", "admin")


class ToolError(Exception):
    """A Danish message for the user; the call is logged with status error."""


@dataclass
class Caller:
    server: str                 # the endpoint key: app | admin | a dynamic endpoint (app/mcp/endpoints.py)
    principal: Principal        # who; principal.meta carries ip and user agent for audit()
    client_id: str | None       # the MCP client, None from the /admin hub
    via: str = "mcp"            # mcp | hub

    @property
    def user_id(self) -> int:
        return self.principal.user_id


@dataclass
class ToolOutput:
    data: Any
    links: list[dict] = field(default_factory=list)
    text: str | None = None     # optional short Danish summary in front of the JSON


@dataclass
class DraftPlan:
    summary: str                                # Danish, shown in /admin and to the model
    impact: dict                                # {bullets, counts, diff, warnings}
    state: Any                                  # what plan() read; hashed and compared again on confirm
    execute: Callable[[Any], Awaitable[Any]]    # execute(conn) -> result; inside the confirm transaction
    links: list[dict] = field(default_factory=list)


@dataclass
class Handler:
    spec: dict
    run: Callable | None = None
    plan: Callable | None = None


HANDLERS: dict[tuple[str, str], Handler] = {}


def catalog_tools(server: str) -> list[dict]:
    return [t for t in CATALOG["tools"] if t["server"] == server]


def _spec(server: str, name: str) -> dict:
    for t in catalog_tools(server):
        if t["name"] == name:
            return t
    raise RuntimeError(f"{server}:{name} is not in catalog.json — add it there first")


def tool(server: str, name: str):
    def register(fn):
        HANDLERS[(server, name)] = Handler(spec=_spec(server, name), run=fn)
        return fn
    return register


def draft(server: str, name: str):
    def register(fn):
        spec = _spec(server, name)
        assert spec["mode"] == "draft", f"{name} is not a draft tool in the catalog"
        HANDLERS[(server, name)] = Handler(spec=spec, plan=fn)
        return fn
    return register


def check_catalog() -> list[str]:
    """Differences between the catalog and the registered handlers (empty when they agree)."""
    problems = []
    for server in SERVERS:
        listed = {t["name"] for t in catalog_tools(server)}
        registered = {n for s, n in HANDLERS if s == server}
        problems += [f"{server}:{n} has no handler" for n in sorted(listed - registered)]
        problems += [f"{server}:{n} is not in the catalog" for n in sorted(registered - listed)]
    return problems


# ── Schemas ─────────────────────────────────────────────────────────────────

JSON_TYPES = {"int": {"type": "integer"}, "string": {"type": "string"}, "bool": {"type": "boolean"},
              "date": {"type": "string", "format": "date"}}


def input_schema(spec: dict) -> dict:
    props = {p["name"]: {**JSON_TYPES[p["type"]], "description": p.get("desc", "")} for p in spec["params"]}
    required = [p["name"] for p in spec["params"] if not p.get("optional")]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def annotations(spec: dict) -> dict:
    read = spec["mode"] == "read"
    return {"readOnlyHint": read, "destructiveHint": spec["mode"] in ("draft", "confirm"), "idempotentHint": read,
            "openWorldHint": False}


def coerce_args(spec: dict, args: dict | None) -> dict:
    """Validates and converts arguments (clients and the hub send strings for numbers)."""
    args = dict(args or {})
    known = {p["name"]: p for p in spec["params"]}
    unknown = set(args) - set(known)
    if unknown:
        raise ToolError(f"Ukendte parametre: {', '.join(sorted(unknown))}")
    out = {}
    for name, p in known.items():
        value = args.get(name)
        if value is None or value == "":
            if not p.get("optional"):
                raise ToolError(f"Parameteren {name} mangler")
            continue
        try:
            if p["type"] == "int":
                value = int(value)
            elif p["type"] == "bool":
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "ja", "yes")
            else:
                value = str(value)
        except (TypeError, ValueError):
            raise ToolError(f"Parameteren {name} skal være et {p['type']}")
        out[name] = value
    return out


# ── Logging ─────────────────────────────────────────────────────────────────

def mask_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + ":…"
    parts = ip.split(".")
    return ".".join(parts[:3] + ["x"]) if len(parts) == 4 else None


def _truncate(value):
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500] + "…"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value[:50]]
    return value


async def log_call(*, server: str, user_id: int | None, client_id: str | None, method: str, tool: str | None,
                   args: dict | None, ms: int | None, status: str, ip: str | None, cache: str | None = None) -> None:
    async with db.connection() as conn:
        await conn.execute("""
            insert into mcp_log (server, user_id, client_id, method, tool, args, duration_ms, status, ip_masked, cache)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (server, user_id, client_id, method, tool, Jsonb(_truncate(args)) if args is not None else None, ms, status,
             mask_ip(ip), cache))


# ── Invocation ──────────────────────────────────────────────────────────────

def jsonable(value):
    return json.loads(json.dumps(value, default=str))


async def invoke(server: str, name: str, raw_args: dict | None, caller: Caller) -> tuple[dict, bool]:
    """Runs one tool. Returns (result, is_error). Read and write tools return their data; draft tools return
    the draft (nothing changes before confirm_draft)."""
    from app.mcp import drafts  # drafts registers confirm/cancel and imports this module

    from app.mcp import data_tools, endpoints, toolmap

    started = time.monotonic()
    status, args, cache_status = "ok", raw_args, None
    log_args, t = None, None
    try:
        # The endpoint map in the database (governed from /admin/data) decides which tools exist here and who may
        # call them; catalog.json only describes the Code tools' handlers.
        async with db.connection() as conn:
            t = await toolmap.resolve(conn, server, name)
            if t is None:
                raise ToolError(f"Ukendt værktøj: {name}")
            decision = await toolmap.decide(conn, t, caller.user_id, caller.principal.session_id)
        if not decision["allowed"]:
            raise PermissionError(decision["message"])
        spec = toolmap.spec_for(t, server)
        args = coerce_args(spec, raw_args)
        handler = HANDLERS.get((endpoints.handler_server(server), name))
        if t["kind"] == "data":
            out: ToolOutput = await data_tools.run(t, args, caller, decision["grant"])
            result = {**out.data, **({"summary": out.text} if out.text else {})}
        elif t["kind"] == "operation":
            # Gær Developer: the same operation as the API (app/devkit/operations.py); masked before it leaves.
            from app.mcp import operation_tools
            log_args = operation_tools.log_args(t, args)
            result = await operation_tools.run(t, args, caller)
            cache_status = result.pop("_cache", None)
        elif handler.plan is not None:
            result = await drafts.create(server, name, args, caller)
            status = "draft"
        else:
            out = await handler.run(args, caller)
            result = {**(out.data if isinstance(out.data, dict) else {"result": out.data}),
                      **({"links": out.links} if out.links else {})}
            if out.text:
                result = {"summary": out.text, **result}
            if name == "confirm_draft":
                status = "confirmed"
            elif name == "cancel_draft":
                status = "cancelled"
        return jsonable(result), False
    except ToolError as e:
        status = "error"
        return {"error": str(e)}, True
    except PermissionError as e:
        status = "forbidden"
        return {"error": str(e) or "Ikke tilladt"}, True
    except HTTPException as e:  # shared code from routes/ raises these
        status = "forbidden" if e.status_code == 403 else "error"
        return {"error": str(e.detail)}, True
    finally:
        if log_args is None and isinstance(args, dict) and t is not None and t["kind"] == "operation":
            from app.mcp import operation_tools
            log_args = operation_tools.log_args(t, args)
        await log_call(server=server, user_id=caller.user_id, client_id=caller.client_id, method="tools/call", tool=name,
                       args=log_args if log_args is not None else (args if isinstance(args, dict) else None),
                       ms=int((time.monotonic() - started) * 1000), status=status, ip=caller.principal.meta.ip, cache=cache_status)


async def tool_list(server: str) -> list[dict]:
    """The tools one endpoint offers (the map in the database), in catalog form, as the hub and tools/list show them."""
    from app.mcp import toolmap

    async with db.connection() as conn:
        tools = await toolmap.for_endpoint(conn, server)
    out = []
    for t in tools:
        if t["kind"] == "code" and not toolmap.has_handler(server, t["name"]):
            continue
        spec = toolmap.spec_for(t, server)
        out.append({**spec, "inputSchema": input_schema(spec)})
    return out
