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

from app import captcha, config, db, events, jobs, killswitch, mail, og, retention, settings_store, storage, webhooks
from app import license as gaer_license
from app.canonical_host import CanonicalHostMiddleware
from app import uploads  # noqa: F401  (registers the files.unzip, files.uploads_cleanup and storage.cors jobs)
from app.mailbox import tasks as mailbox_tasks  # noqa: F401  (registers the mail.* jobs and the Lettermint handler)
from app.mcp import hub as mcp_hub
from app.mcp import oauth as mcp_oauth
from app.mcp import transport as mcp_transport
from app import route_manifest
from app.routes import access as access_routes
from app.routes import (admin, admin_files, admin_ops, adoption, ai, app_store, auth, clips, consumption, data, features, files,
                        hooks, impersonation, knowledge, knowledge_admin, me, mcp_tools, nats, office, public, resources, review,
                        security, studio, user_area, web)
from app.routes import admin_mail
from app.routes import admin_secrets
from app.routes import groups as group_routes
from app.routes import admin_messages
from app.routes import messages as messages_routes
from app.routes import admin_meta
from app.routes import meta as meta_routes
from app.routes import mail as mail_routes
from app.routes import admin_users
from app.routes import developer as developer_routes
from app.routes import developer_data as developer_data_routes
from app.routes import developer_studio as developer_studio_routes
from app.routes import yubikey as yubikey_routes
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


# Uploads go from the browser straight to Object Storage (app/uploads.py), so the bucket's origin may be fetched.
UPLOAD_ORIGIN = f" {storage.public_origin()}" if storage.public_origin() else ""


def csp_for(path: str) -> str:
    ancestors = "'self'"
    scripts = "'self'"
    if path.startswith(EMBEDDABLE):
        ancestors = f"'self' {EXTENSION_HOSTS} {OFFICE_HOSTS}"
    if path.startswith("/office-addin"):
        scripts = "'self' https://appsforoffice.microsoft.com"
    return (f"default-src 'self'; script-src {scripts}; img-src 'self' data: blob: https:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            f"connect-src 'self'{UPLOAD_ORIGIN}; frame-src 'self' https://*.officeapps.live.com https://*.office.com; "
            f"frame-ancestors {ancestors}; base-uri 'self'; form-action 'self'; object-src 'none'")


_ID_SEGMENT = re.compile(r"/(\d+|[0-9a-fA-F-]{32,36})(?=/|$)")


def _route(request: Request) -> str:
    """The route template (/api/files/{file_id}) for an event, never the raw path with ids in it."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return (template or _ID_SEGMENT.sub("/{id}", request.url.path))[:200]


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
    if not (lic := gaer_license.current()).valid:
        log.warning("license: %s (%s) — /w pages say this installation is unlicensed", lic.status, lic.reason)
    await db.open_pools()
    workers = jobs.start() if retention.enabled() else None
    if workers and storage.public_origin():
        # The browser uploads straight to the bucket: make sure its CORS lets ORIGIN do that (once per start).
        async with db.connection() as conn:
            await jobs.enqueue(conn, "storage.cors", dedupe_key="storage.cors", max_attempts=3)
    event_loops = events.start(maintenance=retention.enabled())
    async with mcp_sessions(app):
        yield
    await events.stop_all(*event_loops)
    if workers:
        await jobs.stop_all(*workers)
    await db.close_pools()


@asynccontextmanager
async def mcp_sessions(app: FastAPI):
    """Runs the session managers of /mcp and /mcp/admin (one each per process) and the task group the dynamic
    /mcp/<key> endpoints start theirs in (app/mcp/transport.py DynamicEndpoints)."""
    async with AsyncExitStack() as stack:
        for endpoint in app.state.mcp.values():
            await stack.enter_async_context(endpoint.manager.run())
        await stack.enter_async_context(app.state.mcp_dynamic.run())
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
        # Kill switches (app.killswitch): a closed part of the service answers 503; /admin and login never close.
        if killswitch.may_block(path) and (closed := await killswitch.blocked(request.method, path)):
            await events.emit("http.blocked", ref=closed["key"])
            return JSONResponse({"detail": f"{closed['label']} er midlertidigt lukket. Prøv igen senere.",
                                 "code": "kill_switch", "switch": closed["key"]},
                                status_code=503, headers={"Retry-After": "60", "Cache-Control": "no-store"})
        try:
            response = await call_next(request)
        except Exception:
            await events.emit("http.5xx", ref=_route(request))
            raise
        if response.status_code >= 500 or response.status_code == 429:
            await events.emit("http.5xx" if response.status_code >= 500 else "http.429", ref=_route(request))
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

    # The license statement in the <head> of every /w page (app.license); outermost, after the guard.
    app.add_middleware(gaer_license.LicenseHeadMiddleware)
    # Alias domains redirect to ORIGIN before anything else runs (app.canonical_host); outermost.
    app.add_middleware(CanonicalHostMiddleware)

    @app.get("/health")
    async def health():
        async with db.connection() as conn:
            await conn.execute("select 1")
        return {"ok": True, "version": config.settings.version, "license": gaer_license.current().public()}

    for r in (auth.router, ai.router, me.router, security.router, files.router, clips.router, public.router,
              admin_files.router, admin.router,  # admin_files first: /files/folders before /files/{file_id}
              admin_users.router, yubikey_routes.admin_router, yubikey_routes.router,
              app_store.router, mcp_tools.router, data.router, studio.router, access_routes.router, features.router, features.admin_router,
              impersonation.router, mcp_oauth.router,
              mcp_hub.router, office.router, review.router, admin_ops.router, hooks.router, nats.router, resources.router, consumption.router,
              user_area.router, mail_routes.router, admin_mail.router, adoption.router, adoption.admin_router,
              knowledge.router, knowledge_admin.router, group_routes.router, group_routes.me_router, admin_secrets.router,
              meta_routes.router, admin_meta.router,
              messages_routes.router, admin_messages.router, developer_routes.router, developer_data_routes.router,
              developer_studio_routes.router,
              web.router, files.memory_router):
        app.include_router(r)

    # MCP: exact routes, so /mcp/authorize, /mcp/token and /mcp/register above stay ordinary endpoints. The dynamic
    # endpoints from /developer/studio/mcp answer on /mcp/<key> after them (reserved keys are refused there).
    app.state.mcp = mcp_transport.endpoints()
    app.state.mcp_dynamic = mcp_transport.DynamicEndpoints()
    app.router.routes.append(Route("/mcp/admin", endpoint=app.state.mcp["admin"], methods=["GET", "POST", "DELETE"]))
    app.router.routes.append(Route("/mcp", endpoint=app.state.mcp["app"], methods=["GET", "POST", "DELETE"]))
    app.router.routes.append(Route("/mcp/{key}", endpoint=app.state.mcp_dynamic, methods=["GET", "POST", "DELETE"]))

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
