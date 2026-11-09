"""Streamable HTTP for /mcp and /mcp/admin with the official `mcp` SDK (low-level Server, stateful sessions).

Every request: bearer token → grant (oauth.verify_access_token) → Caller, put on the ASGI scope so the tool
handler of that very request sees it. /mcp needs scope `mcp`; /mcp/admin needs `mcp:admin` and the admin role,
checked again on every request (not a test account, never while impersonating — such tokens do not exist).
"""

import json
import time

import mcp_types as types
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import config, db
from app.logs import RequestMeta
from app.mcp import oauth, toolmap
from app.mcp.registry import CATALOG, HANDLERS, Caller, annotations, input_schema, invoke, log_call
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
    s = config.settings
    return s.mcp_admin_enabled if server == "admin" else s.mcp_app_enabled


def _caller(scope) -> Caller:
    caller = scope.get("gaer_mcp_caller")
    if caller is None:
        raise PermissionError("Ingen MCP-session")
    return caller


def build_server(server: str) -> Server:
    info = CATALOG["servers"][server]

    async def list_tools(ctx, params):
        caller = _caller(ctx.request.scope)
        await log_call(server=server, user_id=caller.user_id, client_id=caller.client_id, method="tools/list", tool=None,
                       args=None, ms=0, status="ok", ip=caller.principal.meta.ip)
        tools = []
        async with db.connection() as conn:
            for t in await toolmap.for_endpoint(conn, server):
                if t["kind"] == "code" and (server, t["name"]) not in HANDLERS:
                    continue
                spec = toolmap.spec_for(t, server)
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
                  instructions=INSTRUCTIONS[server], on_list_tools=list_tools, on_call_tool=call_tool)


class McpEndpoint:
    """ASGI app for one MCP server. Mounted as an exact route (/mcp, /mcp/admin) in main.py."""

    def __init__(self, server: str):
        self.server = server
        self.manager = StreamableHTTPSessionManager(build_server(server), json_response=True)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        if not enabled(self.server):
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        request = Request(scope)
        started = time.monotonic()
        auth = request.headers.get("authorization", "")
        grant = await oauth.verify_access_token(auth[7:]) if auth.lower().startswith("bearer ") else None
        if grant is None:
            await log_call(server=self.server, user_id=None, client_id=None, method=request.method, tool=None, args=None,
                           ms=int((time.monotonic() - started) * 1000), status="auth_failed", ip=client_ip(request))
            metadata = f"{oauth.origin()}/.well-known/oauth-protected-resource{'/mcp/admin' if self.server == 'admin' else '/mcp'}"
            await JSONResponse({"error": "invalid_token", "error_description": "Log ind via OAuth"}, status_code=401,
                               headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'})(scope, receive, send)
            return
        needed = oauth.scope_for(self.server)
        # /mcp/admin's tools span every part of /admin, so it needs the admin role 'fuld' (app/admin_roles.py).
        if grant.scope != needed or (self.server == "admin" and (grant.role != "admin" or "fuld" not in grant.admin_roles)):
            await log_call(server=self.server, user_id=grant.user_id, client_id=grant.client_id, method=request.method,
                           tool=None, args=None, ms=0, status="forbidden", ip=client_ip(request))
            await JSONResponse({"error": "insufficient_scope", "error_description": "Kræver admin-rollen med fuld adgang"
                                if self.server == "admin" else "Forkert scope"}, status_code=403,
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


def endpoints() -> dict[str, McpEndpoint]:
    # Importing the tool modules registers their handlers.
    from app.mcp import access_tools, admin_server, app_server, drafts  # noqa: F401
    return {"app": McpEndpoint("app"), "admin": McpEndpoint("admin")}
