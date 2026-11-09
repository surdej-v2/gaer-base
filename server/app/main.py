"""Gær server: API under /api, the built Vite frontend for everything else, /health for deploys."""

import logging
import os
import re
from html import escape
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from app import captcha, config, db, jobs, mail, og, retention, settings_store, webhooks
from app.mailbox import tasks as mailbox_tasks  # noqa: F401  (registers the mail.* jobs and the Lettermint handler)
from app.mcp import hub as mcp_hub
from app.mcp import oauth as mcp_oauth
from app.mcp import transport as mcp_transport
from app import route_manifest
from app.routes import access as access_routes
from app.routes import (admin, admin_files, admin_ops, ai, app_store, auth, clips, consumption, data, features, files, hooks,
                        impersonation, knowledge, knowledge_admin, me, mcp_tools, nats, office, public, review, security,
                        user_area, web)
from app.routes import admin_mail
from app.routes import mail as mail_routes
from app.security import bootstrap_admin_email, bootstrap_admin_phone
from app.sessions import optional_principal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# Requests that change state must carry this header. Browsers cannot add it cross-site
# without a CORS preflight, which we never allow, so it stops CSRF together with SameSite cookies.
CSRF_HEADER = "x-gaer"
CSRF_EXEMPT = {"/api/collect"}  # sendBeacon cannot set headers; the endpoint only appends anonymous events
CSRF_EXEMPT_PREFIXES = ("/api/hooks/in/",)  # incoming webhooks: other systems, no cookie, verified per endpoint

# Who may embed us: the app itself (heatmap previews, the Office shell) and, for the embeddable
# routes, the browser extension and the Office hosts (Office on the web runs add-ins in iframes).
OFFICE_HOSTS = ("https://*.officeapps.live.com https://*.office.com https://*.office365.com https://*.office.net "
                "https://*.microsoft.com https://*.sharepoint.com https://outlook.live.com")
EXTENSION_HOSTS = "chrome-extension: moz-extension: safari-web-extension:"
EMBEDDABLE = route_manifest.EMBEDDABLE  # routes with "embeddable": true in web/src/routes.json


def csp_for(path: str) -> str:
    ancestors = "'self'"
    scripts = "'self'"
    if path.startswith(EMBEDDABLE):
        ancestors = f"'self' {EXTENSION_HOSTS} {OFFICE_HOSTS}"
    if path.startswith("/office-addin"):
        scripts = "'self' https://appsforoffice.microsoft.com"
    return (f"default-src 'self'; script-src {scripts}; img-src 'self' data: blob: https:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            f"connect-src 'self'; frame-src 'self' https://*.officeapps.live.com https://*.office.com; "
            f"frame-ancestors {ancestors}; base-uri 'self'; form-action 'self'; object-src 'none'")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("gaer")
    # Both raise on an invalid BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PHONE.
    bootstrap = bootstrap_admin_email() or (bootstrap_admin_phone() if config.settings.sms_login else None)
    if config.settings.is_production and not bootstrap:
        log.warning("BOOTSTRAP_ADMIN_EMAIL is not set: the first person to log in on a new instance becomes super admin")
    if config.settings.demo_mode:
        log.warning("DEMO_MODE is on: login codes are written to this log instead of being sent")
    elif missing := mail.missing_config():
        log.warning("e-mail is not configured, login codes cannot be sent: missing %s", ", ".join(missing))
    if config.settings.is_production and not captcha.enabled():
        log.warning("CAPTCHA=off: account creation and login with a code have no robot check")
    await db.open_pools()
    workers = jobs.start() if retention.enabled() else None
    async with mcp_sessions(app):
        yield
    if workers:
        await jobs.stop_all(*workers)
    await db.close_pools()


@asynccontextmanager
async def mcp_sessions(app: FastAPI):
    """Runs the session managers of /mcp and /mcp/admin (one each per process)."""
    async with AsyncExitStack() as stack:
        for endpoint in app.state.mcp.values():
            await stack.enter_async_context(endpoint.manager.run())
        yield


_html_cache: dict[str, tuple[float, str]] = {}


async def app_html(page: str, path: str) -> str:
    """index.html with the page's title and sharing tags (og:image etc., app/og.py), so links to /login,
    /privatliv and the app's pages get a preview when they are shared."""
    mtime = os.path.getmtime(page)
    cached = _html_cache.get(page)
    if not cached or cached[0] != mtime:
        with open(page, encoding="utf-8") as f:
            cached = _html_cache[page] = (mtime, f.read())
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
    name = values["app_name"]
    route = route_manifest.route_for(path)
    title = f"{route['title']} · {name}" if route and path != "/" else name
    surface = og.surface_for(path)
    tags = og.meta_tags(title=title, description=og.DESCRIPTIONS.get(surface, ""), site_name=name,
                        image=og.image_url(surface, values["og_images"]),
                        url=config.settings.origin.rstrip("/") + (path if route else "/"))
    html = re.sub(r"<title>.*?</title>", lambda _: f"<title>{escape(title)}</title>", cached[1], count=1)
    return html.replace("</head>", f"{tags}\n</head>", 1)


def create_app() -> FastAPI:
    app = FastAPI(title="Tørgær", lifespan=lifespan, docs_url="/api/docs" if not config.settings.is_production else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if not config.settings.is_production else None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if (path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
                and path not in CSRF_EXEMPT and not path.startswith(CSRF_EXEMPT_PREFIXES)
                and request.headers.get(CSRF_HEADER) != "1"):
            return JSONResponse({"detail": "Mangler X-Gaer header"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", csp_for(path))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # Microphone for our own pages only: the review panel records and dictates notes.
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
        if config.settings.secure_cookies:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/health")
    async def health():
        async with db.connection() as conn:
            await conn.execute("select 1")
        return {"ok": True, "version": config.settings.version}

    for r in (auth.router, ai.router, me.router, security.router, files.router, clips.router, public.router,
              admin_files.router, admin.router,  # admin_files first: /files/folders before /files/{file_id}
              app_store.router, mcp_tools.router, data.router, access_routes.router, features.router, features.admin_router,
              impersonation.router, mcp_oauth.router,
              mcp_hub.router, office.router, review.router, admin_ops.router, hooks.router, nats.router, consumption.router,
              user_area.router, mail_routes.router, admin_mail.router,
              knowledge.router, knowledge_admin.router,
              web.router, files.memory_router):
        app.include_router(r)

    # MCP: exact routes, so /mcp/authorize, /mcp/token and /mcp/register above stay ordinary endpoints.
    app.state.mcp = mcp_transport.endpoints()
    app.router.routes.append(Route("/mcp/admin", endpoint=app.state.mcp["admin"], methods=["GET", "POST", "DELETE"]))
    app.router.routes.append(Route("/mcp", endpoint=app.state.mcp["app"], methods=["GET", "POST", "DELETE"]))

    static_dir = os.path.abspath(config.settings.static_dir)
    index = os.path.join(static_dir, "index.html")
    admin_index = os.path.join(static_dir, "admin.html")
    if os.path.isdir(os.path.join(static_dir, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if full_path == "" and await optional_principal(request) is None:
            return RedirectResponse("/w/", status_code=302)  # anonymous visitors get the public website
        candidate = os.path.abspath(os.path.join(static_dir, full_path))
        if full_path and candidate.startswith(static_dir + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        # /admin is a separate Vite entry (web/admin.html); everything else is the app on / (web/src/routes.json).
        page = admin_index if route_manifest.app_for("/" + full_path) == "admin" else index
        if not os.path.isfile(page):
            return JSONResponse({"detail": "Frontend er ikke bygget. Kør npm run build i web/."}, status_code=404)
        if page == admin_index:
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(await app_html(page, "/" + full_path), headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
