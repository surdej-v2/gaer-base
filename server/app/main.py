"""Gær server: API under /api, the built Vite frontend for everything else, /health for deploys."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config, db, retention
from app.routes import admin, ai, auth, clips, files, me, office, public, web
from app.security import bootstrap_admin_phone
from app.sessions import optional_principal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# Requests that change state must carry this header. Browsers cannot add it cross-site
# without a CORS preflight, which we never allow, so it stops CSRF together with SameSite cookies.
CSRF_HEADER = "x-gaer"
CSRF_EXEMPT = {"/api/collect"}  # sendBeacon cannot set headers; the endpoint only appends anonymous events

# Who may embed us: the app itself (heatmap previews, the Office shell) and, for the embeddable
# routes, the browser extension and the Office hosts (Office on the web runs add-ins in iframes).
OFFICE_HOSTS = ("https://*.officeapps.live.com https://*.office.com https://*.office365.com https://*.office.net "
                "https://*.microsoft.com https://*.sharepoint.com https://outlook.live.com")
EXTENSION_HOSTS = "chrome-extension: moz-extension: safari-web-extension:"
EMBEDDABLE = ("/extension", "/office-addin", "/login", "/assets/", "/api/")


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
async def lifespan(_: FastAPI):
    bootstrap = bootstrap_admin_phone()  # raises on an invalid BOOTSTRAP_ADMIN_PHONE
    if config.settings.is_production and not bootstrap:
        logging.getLogger("gaer").warning(
            "BOOTSTRAP_ADMIN_PHONE is not set: the first person to log in on a new instance becomes admin")
    await db.open_pools()
    task = asyncio.create_task(retention.maintenance_loop()) if retention.enabled() else None
    yield
    if task:
        task.cancel()
    await db.close_pools()


def create_app() -> FastAPI:
    app = FastAPI(title="Gær", lifespan=lifespan, docs_url="/api/docs" if not config.settings.is_production else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if not config.settings.is_production else None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if (path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
                and path not in CSRF_EXEMPT and request.headers.get(CSRF_HEADER) != "1"):
            return JSONResponse({"detail": "Mangler X-Gaer header"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", csp_for(path))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
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

    for r in (auth.router, ai.router, me.router, files.router, clips.router, public.router, admin.router, office.router,
              web.router, files.memory_router):
        app.include_router(r)

    static_dir = os.path.abspath(config.settings.static_dir)
    index = os.path.join(static_dir, "index.html")
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
        if os.path.isfile(index):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Frontend er ikke bygget. Kør npm run build i web/."}, status_code=404)

    return app


app = create_app()
