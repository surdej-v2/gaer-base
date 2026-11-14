"""Streamable HTTP for /mcp, /mcp/admin and the dynamic /mcp/<key> endpoints with the official `mcp` SDK
(low-level Server, stateful sessions).

Every request: bearer token → grant (oauth.verify_access_token) → Caller, put on the ASGI scope so the tool
handler of that very request sees it. /mcp needs scope `mcp`; /mcp/admin needs `mcp:admin` and the admin role,
checked again on every request (not a test account, never while impersonating — such tokens do not exist).
A dynamic endpoint (app/mcp/endpoints.py, made in /developer/studio/mcp) works like /mcp — scope `mcp`, the app's
handlers — and checks its own audience on every request; its session manager starts the first time it is called.
"""

import contextlib
import json
import time

import anyio
from anyio.abc import TaskGroup

import mcp_types as types
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import config, db
from app.logs import RequestMeta
from app.mcp import endpoints as endpoints_mod
from app.mcp import oauth, toolmap
from app.mcp.registry import CATALOG, Caller, annotations, input_schema, invoke, log_call
from app.security import client_ip, user_agent
from app.sessions import Principal

INSTRUCTIONS = {
    "app": ("Tørgær for den bruger, der loggede ind: klip, filer, features og privatliv. Du ser og ændrer kun brugerens egne "
            "data. Sletning og GDPR-eksport giver en kladde: vis opsummeringen og kald confirm_draft(draftId, reason), "
            "først når brugeren har sagt ja. Svar indeholder links (links[]) til den rigtige skærm — tag dem med."),
    "admin": ("Admin af denne Tørgær-instans. Læseværktøjer svarer med det samme. Alle ændringer giver en kladde med "
              "opsummering og konsekvens: vis den for brugeren og kald confirm_draft(draftId, reason) først efter et ja. "
              "Svar indeholder deep links (links[]) der åbner den rigtige skærm i /admin — tag dem med. "
              "Start med nav_sections eller nav_resolve, hvis du ikke ved, hvor noget ligger."),
}


def enabled(server: str) -> bool:
    """The built-in switches; a dynamic endpoint also needs the app's switch (it is authorized like /mcp)."""
    s = config.settings
    return s.mcp_admin_enabled if server == "admin" else s.mcp_app_enabled


def _caller(scope) -> Caller:
    caller = scope.get("gaer_mcp_caller")
    if caller is None:
        raise PermissionError("Ingen MCP-session")
    return caller


def build_server(server: str, info: dict | None = None) -> Server:
    """`info`: {name, label, instructions} — from the catalog for app/admin, from mcp_endpoints for a dynamic one."""
    info = info or {**CATALOG["servers"][server], "instructions": INSTRUCTIONS[server]}

    async def list_tools(ctx, params):
        caller = _caller(ctx.request.scope)
        await log_call(server=server, user_id=caller.user_id, client_id=caller.client_id, method="tools/list", tool=None,
                       args=None, ms=0, status="ok", ip=caller.principal.meta.ip)
        tools = []
        async with db.connection() as conn:
            for t in await toolmap.for_endpoint(conn, server):
                if t["kind"] == "code" and not toolmap.has_handler(server, t["name"]):
                    continue
                spec = toolmap.spec_for(t, server)
                if t["kind"] == "operation" and t["classification"] != "white" and caller.principal.role != "admin":
                    # MCP-5: external users only see white tools from Gær Developer.
                    who = await db.fetch_one(conn, "select audience from users where id = %s", (caller.user_id,))
                    if not who or who["audience"] != "internal":
                        continue
                if t["classification"] != "white" and t["name"] not in toolmap.CORE:
                    d = await toolmap.decide(conn, t, caller.user_id, caller.principal.session_id)
                    if not d["allowed"]:
                        spec = {**spec, "description": f"{spec['description']} [Låst: {d['message']}]"}
                hints = annotations(spec)
                tools.append(types.Tool(name=spec["name"], description=spec["description"], input_schema=input_schema(spec),
                                        annotations=types.ToolAnnotations(read_only_hint=hints["readOnlyHint"],
                                                                          destructive_hint=hints["destructiveHint"],
                                                                          idempotent_hint=hints["idempotentHint"],
                                                                          open_world_hint=False)))
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        caller = _caller(ctx.request.scope)
        result, is_error = await invoke(server, params.name, params.arguments, caller)
        text = json.dumps(result, ensure_ascii=False, indent=1)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error,
                                    structured_content=result)

    return Server(info["name"], version=config.settings.version, title=f"Tørgær · {info['label']}",
                  instructions=info["instructions"] or INSTRUCTIONS["app"], on_list_tools=list_tools, on_call_tool=call_tool)


class McpEndpoint:
    """ASGI app for one MCP server: the built-in /mcp and /mcp/admin (exact routes in main.py) or one dynamic
    /mcp/<key> (`row` from mcp_endpoints, handed over by DynamicEndpoints)."""

    def __init__(self, server: str, row: dict | None = None):
        self.server = server
        self.row = row
        info = {"name": row["key"], "label": row["name"], "instructions": row["instructions"]} if row else None
        self.manager = StreamableHTTPSessionManager(build_server(server, info), json_response=True)

    def _refused(self, grant) -> str | None:
        """Why this token may not use the endpoint (None when it may)."""
        if grant.scope != oauth.scope_for(self.server):
            return "Forkert scope"
        # /mcp/admin's tools span every part of /admin, so it needs the admin role 'fuld' (app/admin_roles.py).
        if self.server == "admin" and (grant.role != "admin" or "fuld" not in grant.admin_roles):
            return "Kræver admin-rollen med fuld adgang"
        if self.row and not endpoints_mod.may_connect(self.row, role=grant.role, audience=grant.audience):
            return f"Endpointet er for: {endpoints_mod.AUDIENCES[self.row['audience']].lower()}"
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        if not enabled(self.server) or (self.row and not self.row["enabled"]):
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        request = Request(scope)
        started = time.monotonic()
        auth = request.headers.get("authorization", "")
        grant = await oauth.verify_access_token(auth[7:]) if auth.lower().startswith("bearer ") else None
        if grant is None:
            await log_call(server=self.server, user_id=None, client_id=None, method=request.method, tool=None, args=None,
                           ms=int((time.monotonic() - started) * 1000), status="auth_failed", ip=client_ip(request))
            metadata = f"{oauth.origin()}/.well-known/oauth-protected-resource{endpoints_mod.path_for(self.server)}"
            await JSONResponse({"error": "invalid_token", "error_description": "Log ind via OAuth"}, status_code=401,
                               headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'})(scope, receive, send)
            return
        needed = oauth.scope_for(self.server)
        if refused := self._refused(grant):
            await log_call(server=self.server, user_id=grant.user_id, client_id=grant.client_id, method=request.method,
                           tool=None, args=None, ms=0, status="forbidden", ip=client_ip(request))
            await JSONResponse({"error": "insufficient_scope", "error_description": refused}, status_code=403,
                               headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{needed}"'})(
                scope, receive, send)
            return
        principal = Principal(session_id=grant.session_id, user_id=grant.user_id, email=grant.email, phone=grant.phone,
                              role=grant.role,
                              scope="full", display_name=grant.display_name,
                              meta=RequestMeta(ip=client_ip(request), user_agent=user_agent(request)),
                              super_admin=grant.super_admin, admin_roles=grant.admin_roles)
        scope["gaer_mcp_caller"] = Caller(server=self.server, principal=principal, client_id=grant.client_id)
        # The SDK binds each MCP session to the credential that opened it.
        scope["user"] = AuthenticatedUser(AccessToken(token="-", client_id=grant.client_id, scopes=[grant.scope],
                                                      subject=f"{grant.user_id}:{grant.session_id}"))
        await self.manager.handle_request(scope, receive, send)


class DynamicEndpoints:
    """ASGI app for /mcp/{key}: looks the endpoint up in the database on every request (so a switch-off, an edit or
    a deletion in /developer/studio/mcp takes effect at once) and hands the request to a McpEndpoint per key.
    Their session managers start on first use inside the task group that run() holds open for the process."""

    def __init__(self):
        self.endpoints: dict[str, McpEndpoint] = {}
        self._stops: dict[str, anyio.Event] = {}
        self._tg: TaskGroup | None = None

    @contextlib.asynccontextmanager
    async def run(self):
        async with anyio.create_task_group() as tg:
            self._tg = tg
            try:
                yield
            finally:
                self._tg = None
                for stop in self._stops.values():
                    stop.set()

    async def _start(self, row: dict) -> McpEndpoint:
        if self._tg is None:
            raise RuntimeError("MCP endpoints are not running (mcp_sessions)")
        endpoint, ready, stop = McpEndpoint(row["key"], row), anyio.Event(), anyio.Event()

        async def run_manager():
            async with endpoint.manager.run():
                ready.set()
                await stop.wait()

        self._tg.start_soon(run_manager)
        await ready.wait()
        self.endpoints[row["key"]], self._stops[row["key"]] = endpoint, stop
        return endpoint

    def _stop(self, key: str) -> None:
        if stop := self._stops.pop(key, None):
            stop.set()
        self.endpoints.pop(key, None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        key = scope.get("path_params", {}).get("key", "")
        row = None
        if endpoints_mod.valid_key(key):
            async with db.connection() as conn:
                row = await endpoints_mod.get(conn, key)
        if row is None or row["builtin"]:
            self._stop(key)
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        endpoint = self.endpoints.get(key)
        # A new name or new instructions mean a new Server (initialize answers with them): open sessions start over.
        if endpoint and (endpoint.row["name"], endpoint.row["instructions"]) != (row["name"], row["instructions"]):
            self._stop(key)
            endpoint = None
        if endpoint is None:
            endpoint = await self._start(row)
        endpoint.row = row
        await endpoint(scope, receive, send)


def endpoints() -> dict[str, McpEndpoint]:
    # Importing the tool modules registers their handlers.
    from app.mcp import access_tools, admin_server, app_server, drafts  # noqa: F401
    return {"app": McpEndpoint("app"), "admin": McpEndpoint("admin")}
