"""MCP endpoints: the built-in /mcp ("app") and /mcp/admin ("admin"), and the ones defined at runtime in
/developer/studio/mcp (/mcp/<key>). All of them are rows in mcp_endpoints (migration 0030); the endpoint map
(mcp_tool_endpoints, toolmap.py) says which tools each one offers.

A dynamic endpoint acts as the user who connects, exactly like /mcp: OAuth scope "mcp", the app's Code tool handlers
and Data tools from the catalog. `audience` decides who may connect at all; the tool classifications decide who may
call which tool (specs/mcp/ACCESS.md).
"""

import re

from app import db

KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
BUILTIN = ("app", "admin")
# Paths under /mcp that are ordinary routes (oauth.py) or built-in servers: no dynamic endpoint may take them.
RESERVED = {"app", "admin", "register", "authorize", "token", "consent", "data", "studio", "developer", "well-known"}
AUDIENCES = {"all": "Alle loggede brugere", "internal": "Kun interne brugere", "admin": "Kun admins"}
COLUMNS = "key, name, description, instructions, audience, enabled, builtin, created_by, created_at, updated_at, artifact_id, limits"


def path_for(key: str) -> str:
    return "/mcp" if key == "app" else f"/mcp/{key}"


def handler_server(key: str) -> str:
    """Which set of Code tool handlers an endpoint runs: /mcp/admin its own, every other endpoint the app's."""
    return "admin" if key == "admin" else "app"


def scope_for(key: str) -> str:
    return "mcp:admin" if key == "admin" else "mcp"


def valid_key(key: str) -> bool:
    return bool(KEY_RE.match(key)) and key not in RESERVED


BUILTIN_ROWS = (
    ("app", "Appen", "Tørgær for den bruger, der loggede ind: egne klip, filer, features og privatliv."),
    ("admin", "Admin", "Admin af denne instans. Kræver admin-rollen med fuld adgang; alle ændringer er kladder."),
)


async def ensure_builtin(conn) -> None:
    """The rows for /mcp and /mcp/admin (seeded by migration 0030; put back if a restore or a test emptied the table)."""
    have = await db.fetch_one(conn, "select count(*) as n from mcp_endpoints where builtin")
    if have["n"] >= len(BUILTIN_ROWS):
        return
    for key, name, description in BUILTIN_ROWS:
        await conn.execute("""insert into mcp_endpoints (key, name, description, builtin) values (%s, %s, %s, true)
                              on conflict (key) do nothing""", (key, name, description))


async def get(conn, key: str) -> dict | None:
    return await db.fetch_one(conn, f"select {COLUMNS} from mcp_endpoints where key = %s", (key,))


async def all_endpoints(conn) -> list[dict]:
    await ensure_builtin(conn)
    return await db.fetch_all(conn, f"select {COLUMNS} from mcp_endpoints order by builtin desc, key")


async def keys(conn, enabled_only: bool = False) -> list[str]:
    await ensure_builtin(conn)
    rows = await db.fetch_all(conn, f"select key from mcp_endpoints {'where enabled' if enabled_only else ''} "
                                    "order by builtin desc, key")
    return [r["key"] for r in rows]


def may_connect(endpoint: dict, *, role: str, audience: str | None) -> bool:
    """The endpoint's audience against the user who holds the token."""
    if endpoint["audience"] == "admin":
        return role == "admin"
    if endpoint["audience"] == "internal":
        return audience == "internal" or role == "admin"
    return True


def out(e: dict) -> dict:
    return {"key": e["key"], "path": path_for(e["key"]), "name": e["name"], "description": e["description"],
            "instructions": e["instructions"], "audience": e["audience"], "audienceLabel": AUDIENCES[e["audience"]],
            "enabled": e["enabled"], "builtin": e["builtin"], "scope": scope_for(e["key"]),
            "createdAt": e["created_at"], "updatedAt": e["updated_at"], "artifactId": e.get("artifact_id")}
