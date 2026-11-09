"""Administrative roles: who may use which part of /admin.

An admin (users.role = 'admin') has one or more roles in users.admin_roles. Each role opens a set of /admin
sections; each section names the API paths it uses. That one table decides both what the menu shows
(/api/auth/me → admin_sections) and what require_admin lets through, so the two cannot drift apart.

- `fuld` opens everything, including the things only it may do: changing who is admin and which roles they have,
  and the /mcp/admin MCP server (its tools span every section).
- Statistik (/admin) is open to every admin.
- Paths under /api/admin that no section names are open to `fuld` only (deny by default).
The super admin always has `fuld`.
"""

import re

FULL = "fuld"

ROLES: dict[str, dict] = {
    FULL: {"label": "Fuld adgang", "description": "Alt i /admin, også hvem der er admin og med hvilke roller, og MCP for admin."},
    "support": {"label": "Support", "description": "Hjælper brugere: konti, spærring, nulstilling af login, efterligning og auth-loggen."},
    "indhold": {"label": "Indhold", "description": "Website, filer, App Stores og Viden (vidensrum og agenter)."},
    "produkt": {"label": "Produkt", "description": "Features og ringe, AI, analytics og heatmaps."},
    "data": {"label": "Data", "description": "Datakatalog, Udforsk, API-kilder og MCP-værktøjer (hvem må bruge hvad)."},
    "privatliv": {"label": "Privatliv", "description": "GDPR-forespørgsler, samtykke, sletning af logdata, audit- og auth-log, og godkendelse af adgang til fortrolige data."},
    "drift": {"label": "Drift", "description": "MCP-servere, udvidelse og Office, webhooks, jobs, NATS og indstillinger."},
}

# /admin section (path under the router basename) → the API path prefixes it uses.
SECTIONS: dict[str, list[str]] = {
    "/": ["/api/admin/analytics"],
    "/analytics": ["/api/admin/analytics"],
    "/heatmaps": ["/api/admin/heatmaps"],
    "/users": ["/api/admin/users"],
    "/impersonation": ["/api/impersonation"],
    "/logs/auth": ["/api/admin/logs/auth"],
    "/logs/audit": ["/api/admin/logs/audit"],
    "/features": ["/api/admin/features"],
    "/ai": ["/api/admin/ai"],
    "/web": ["/api/admin/web"],
    "/files": ["/api/admin/files"],
    "/knowledge": ["/api/admin/knowledge"],
    "/app-stores": ["/api/admin/app-store"],
    "/data": ["/api/admin/data"],
    "/data/explore": ["/api/admin/data"],
    "/data/connect": ["/api/admin/data"],
    "/data/tools": ["/api/admin/data"],
    "/access": ["/api/admin/access"],
    "/gdpr": ["/api/admin/gdpr"],
    "/consent": ["/api/admin/consent"],
    "/retention": ["/api/admin/retention"],
    "/mcp": ["/api/admin/mcp"],
    "/integrations": [],
    "/webhooks": ["/api/admin/webhooks", "/api/admin/inbound-webhooks"],
    "/jobs": ["/api/admin/jobs", "/api/admin/retention"],
    "/nats": ["/api/admin/nats"],
    "/settings": ["/api/admin/settings"],
}

EVERYONE = ["/"]
ROLE_SECTIONS: dict[str, list[str]] = {
    "support": ["/users", "/impersonation", "/logs/auth"],
    "indhold": ["/web", "/files", "/app-stores", "/knowledge"],
    "produkt": ["/analytics", "/heatmaps", "/features", "/ai"],
    "data": ["/data", "/data/explore", "/data/connect", "/data/tools"],
    "privatliv": ["/gdpr", "/consent", "/retention", "/logs/audit", "/logs/auth", "/access"],
    "drift": ["/mcp", "/integrations", "/webhooks", "/jobs", "/nats", "/settings"],
}

# Only `fuld`, even for roles whose sections contain the path.
FULL_ONLY = [re.compile(r"^/api/admin/users/\d+/(role|admin-roles)$")]
# The plain user list is read by other sections (feature levels, the extension's test-user picker in the top bar).
USER_LIST = "/api/admin/users"
USER_LIST_SECTIONS = {"/users", "/features", "/gdpr", "/retention"}


def clean(roles) -> list[str]:
    return sorted({r for r in (roles or []) if r in ROLES})


def sections(roles) -> list[str]:
    roles = clean(roles)
    if FULL in roles:
        return list(SECTIONS)
    out = list(EVERYONE)
    for r in roles:
        out += [s for s in ROLE_SECTIONS.get(r, []) if s not in out]
    return out


def allows(roles, method: str, path: str) -> bool:
    """May an admin with these roles call this API path?"""
    roles = clean(roles)
    if FULL in roles:
        return True
    if any(p.match(path) for p in FULL_ONLY):
        return False
    open_sections = sections(roles)
    if method == "GET" and path == USER_LIST and USER_LIST_SECTIONS.intersection(open_sections):
        return True
    for section in open_sections:
        for prefix in SECTIONS[section]:
            if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "."):
                return True
    return False


def catalog() -> list[dict]:
    """The roles as /admin shows them when assigning."""
    return [{"key": k, **v, "sections": sections([k])} for k, v in ROLES.items()]
