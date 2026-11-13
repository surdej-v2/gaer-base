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
    "support": {"label": "Support", "description": "Hjælper brugere: konti, grupper, spærring, nulstilling af login, efterligning og auth-loggen; sender beskeder, svarer brugerne og modererer samtaler."},
    "indhold": {"label": "Indhold", "description": "Website, filer, App Stores, Viden (vidensrum og agenter) og træstrukturer."},
    "produkt": {"label": "Produkt", "description": "Features og ringe, AI og AI-forbrug, analytics, heatmaps og adoptionsforløb."},
    "data": {"label": "Data", "description": "Datakatalog, Udforsk, API-kilder og MCP-værktøjer (hvem må bruge hvad), og udviklerportalen /developer (API Studio og MCP Studio)."},
    "privatliv": {"label": "Privatliv", "description": "GDPR-forespørgsler, samtykke, sletning af logdata, audit- og auth-log, godkendelse af adgang til fortrolige data og moderering af samtaler."},
    "drift": {"label": "Drift", "description": "Mail, kalendere og kontakter, hemmeligheder, MCP-servere, udvidelse og Office, webhooks, jobs, NATS, ressourcer og indstillinger; levering af beskeder og deres kanaler."},
}

# /admin section (path under the router basename) → the API path prefixes it uses.
SECTIONS: dict[str, list[str]] = {
    "/": ["/api/admin/analytics"],
    "/analytics": ["/api/admin/analytics"],
    "/heatmaps": ["/api/admin/heatmaps"],
    "/adoption": ["/api/admin/adoption"],
    "/adoption/journeys": ["/api/admin/adoption"],
    "/adoption/routes": ["/api/admin/adoption"],
    "/adoption/console": ["/api/admin/adoption"],
    "/adoption/access": ["/api/admin/adoption"],
    "/users": ["/api/admin/users"],
    "/impersonation": ["/api/impersonation"],
    "/logs/auth": ["/api/admin/logs/auth"],
    "/logs/audit": ["/api/admin/logs/audit"],
    "/features": ["/api/admin/features"],
    "/ai": ["/api/admin/ai"],
    "/consumption": ["/api/admin/consumption"],
    "/web": ["/api/admin/web"],
    "/files": ["/api/admin/files"],
    "/knowledge": ["/api/admin/knowledge"],
    "/meta": ["/api/admin/meta"],
    "/app-stores": ["/api/admin/app-store"],
    "/data": ["/api/admin/data"],
    "/data/explore": ["/api/admin/data"],
    "/data/connect": ["/api/admin/data"],
    "/data/tools": ["/api/admin/data"],
    # Export and import of data: in no role but fuld (a package can carry everyone's data, an import change anything).
    "/data/transfer": ["/api/admin/transfer"],
    # The developer portal on /developer (served by the app on /, not /admin): dashboard, API Studio, MCP Studio.
    "/developer": ["/api/admin/studio"],
    "/access": ["/api/admin/access"],
    "/gdpr": ["/api/admin/gdpr"],
    "/consent": ["/api/admin/consent"],
    "/retention": ["/api/admin/retention"],
    "/mcp": ["/api/admin/mcp"],
    "/integrations": [],
    "/webhooks": ["/api/admin/webhooks", "/api/admin/inbound-webhooks"],
    "/jobs": ["/api/admin/jobs", "/api/admin/retention"],
    "/nats": ["/api/admin/nats"],
    "/mail": ["/api/admin/mail"],
    "/resources": ["/api/admin/resources"],
    "/settings": ["/api/admin/settings"],
    "/groups": ["/api/admin/groups"],
    "/secrets": ["/api/admin/secrets"],
    "/messages": ["/api/admin/messages/overview"],
    "/messages/send": ["/api/admin/messages/broadcast"],
    "/messages/replies": ["/api/admin/messages/replies"],
    "/messages/delivery": ["/api/admin/messages/deliveries"],
    "/messages/settings": ["/api/admin/messages/settings"],
    "/messages/moderation": ["/api/admin/messages/moderation"],
}

EVERYONE = ["/"]
ROLE_SECTIONS: dict[str, list[str]] = {
    "support": ["/users", "/groups", "/impersonation", "/logs/auth", "/messages", "/messages/send", "/messages/replies",
                "/messages/moderation"],
    "indhold": ["/web", "/files", "/app-stores", "/knowledge", "/meta"],
    "produkt": ["/analytics", "/heatmaps", "/features", "/ai", "/consumption", "/adoption", "/adoption/journeys",
                "/adoption/routes", "/adoption/console", "/adoption/access"],
    "data": ["/data", "/data/explore", "/data/connect", "/data/tools", "/developer"],
    "privatliv": ["/gdpr", "/consent", "/retention", "/logs/audit", "/logs/auth", "/access", "/messages/moderation"],
    "drift": ["/mail", "/secrets", "/mcp", "/integrations", "/webhooks", "/jobs", "/nats", "/resources", "/settings",
              "/messages", "/messages/delivery", "/messages/settings"],
}

# Only `fuld`, even for roles whose sections contain the path.
FULL_ONLY = [re.compile(r"^/api/admin/users/\d+/(role|admin-roles)$")]
# The plain user list is read by other sections (feature levels, the extension's test-user picker in the top bar).
USER_LIST = "/api/admin/users"
USER_LIST_SECTIONS = {"/users", "/groups", "/features", "/gdpr", "/retention", "/consumption", "/messages/send"}


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
