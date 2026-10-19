"""Endpoints without login: app config, logo, consent, analytics collection and GDPR requests.

Analytics are collected by this server and stored in the container's own Postgres.
Nothing is sent to third parties. Without consent we only count anonymous page views
(no id, no cookie). With consent the browser sends a random visitor id, which enables
visits, flows, click and scroll heatmaps.
"""

import json
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import db, settings_store
from app.logs import RequestMeta, audit
from app.routes.auth import admin_open_to_anyone
from app.security import classify_device, client_ip, user_agent
from app.sessions import Principal, optional_principal, request_meta
from app.storage import get_storage

router = APIRouter(prefix="/api", tags=["public"])

VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@router.get("/public/config")
async def public_config():
    async with db.connection() as conn:
        values = await settings_store.get_all(conn)
        first_user = await admin_open_to_anyone(conn)
    out = {k: values[k] for k in settings_store.PUBLIC_KEYS}
    # True only when the next person to log in becomes admin (new instance, no BOOTSTRAP_ADMIN_PHONE).
    out["first_user"] = first_user
    out["logo_url"] = "/api/public/logo" if values["logo_key"] else None
    out.pop("logo_key")
    return out


@router.get("/public/logo")
async def logo():
    async with db.connection() as conn:
        key = await settings_store.get(conn, "logo_key")
    if not key:
        raise HTTPException(404)
    data, content_type = await get_storage().get(key)
    return Response(data, media_type=content_type, headers={"Cache-Control": "public, max-age=300"})


class ConsentIn(BaseModel):
    statistics: bool
    visitor_id: str | None = Field(default=None, max_length=64)
    version: int = 1


@router.post("/consent")
async def give_consent(body: ConsentIn, request: Request, p: Principal | None = Depends(optional_principal)):
    if body.visitor_id is not None and not VISITOR_RE.match(body.visitor_id):
        raise HTTPException(400, "Ugyldigt id")
    if body.statistics and not body.visitor_id:
        raise HTTPException(400, "visitor_id mangler")
    device, browser = classify_device(user_agent(request))
    async with db.connection() as conn:
        await conn.execute(
            "insert into consents (visitor_id, user_id, statistics, version, device) values (%s, %s, %s, %s, %s)",
            (body.visitor_id, p.user_id if p else None, body.statistics, body.version, f"{browser} · {device}"))
    return {"ok": True}


async def _has_consent(conn, visitor_id: str) -> bool:
    row = await db.fetch_one(conn, """
        select statistics from consents where visitor_id = %s order by ts desc, id desc limit 1""", (visitor_id,))
    return bool(row and row["statistics"])


def _clean_path(value) -> str | None:
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    return value.split("?")[0].split("#")[0][:300]


def _source(referrer, host: str | None) -> str | None:
    """Keeps only the referring host, so full URLs with personal data are never stored."""
    if not isinstance(referrer, str) or not referrer:
        return None
    netloc = urlparse(referrer).netloc.lower()
    if not netloc or (host and netloc == host.lower()):
        return None
    return netloc[:200]


def _fraction(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, f))


@router.post("/collect", status_code=204)
async def collect(request: Request, p: Principal | None = Depends(optional_principal)):
    """Receives navigator.sendBeacon payloads (text/plain JSON, so no CORS preflight)."""
    try:
        payload = json.loads((await request.body())[:8192] or b"{}")
    except ValueError:
        raise HTTPException(400)
    events = payload if isinstance(payload, list) else [payload]
    ua = user_agent(request)
    device, browser = classify_device(ua)
    if device == "bot":
        return Response(status_code=204)
    host = request.headers.get("host")

    async with db.connection() as conn:
        visitor_id = payload[0].get("visitor_id") if isinstance(payload, list) and payload else \
            (payload.get("visitor_id") if isinstance(payload, dict) else None)
        consented = bool(isinstance(visitor_id, str) and VISITOR_RE.match(visitor_id)
                         and await _has_consent(conn, visitor_id))
        for e in events[:50]:
            if not isinstance(e, dict):
                continue
            kind = e.get("kind")
            path = _clean_path(e.get("path"))
            if kind not in ("pageview", "click", "scroll") or path is None or path.startswith("/admin"):
                continue
            if kind != "pageview" and not consented:
                continue  # clicks and scroll are tied to a visitor and need consent
            session_id = e.get("session_id") if consented and isinstance(e.get("session_id"), str) else None
            await conn.execute("""
                insert into analytics_events (kind, path, visitor_id, session_id, user_id, referrer, prev_path,
                                              device, browser, x, y, depth, viewport_w)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (kind, path,
                 visitor_id if consented else None,
                 session_id[:64] if session_id else None,
                 p.user_id if (consented and p) else None,
                 _source(e.get("referrer"), host) if kind == "pageview" else None,
                 _clean_path(e.get("prev_path")) if consented else None,
                 device, browser,
                 _fraction(e.get("x")) if kind == "click" else None,
                 _fraction(e.get("y")) if kind == "click" else None,
                 _fraction(e.get("depth")) if kind == "scroll" else None,
                 int(e["viewport_w"]) if isinstance(e.get("viewport_w"), (int, float)) else None))
    return Response(status_code=204)


class PublicGdprIn(BaseModel):
    kind: str = Field(pattern="^(access|rectification|erasure)$")
    contact: str = Field(min_length=3, max_length=200)
    message: str = Field(default="", max_length=4000)


@router.post("/public/gdpr")
async def public_gdpr_request(body: PublicGdprIn, request: Request):
    """For people without a login (or who lost access). Rate limited per IP."""
    meta: RequestMeta = request_meta(request)
    async with db.connection() as conn:
        recent = await db.fetch_one(conn, """
            select count(*) as n from audit_log
            where action = 'gdpr.request' and actor_user_id is null and ip = %s
              and ts > now() - interval '1 hour'""", (client_ip(request),))
        if recent["n"] >= 5:
            raise HTTPException(429, "For mange anmodninger. Prøv igen senere.")
        row = await db.fetch_one(conn, """
            insert into gdpr_requests (kind, contact, message) values (%s, %s, %s) returning id, due_at""",
            (body.kind, body.contact.strip(), body.message))
        await audit(conn, "gdpr.request", actor_id=None, meta=meta, table="gdpr_requests", row_id=row["id"],
                    after={"kind": body.kind, "status": "received", "source": "public"})
    return {"id": row["id"], "due_at": row["due_at"]}
