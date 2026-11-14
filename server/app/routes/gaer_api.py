"""/gaer/v<major>/<slug>/… — the REST APIs made in API-studie (app/devkit/apis.py), outside Gær base's own /api routes
(API-1), and /gaer/oauth/token for client credentials (API-6). No cookies: every call carries a key or a token, so
CSRF does not apply. Masking happens in the operation, before the answer leaves (F-10).
"""

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app import config
from app.devkit import apis, cache
from app.mcp import oauth
from app.sessions import request_meta

router = APIRouter(tags=["gaer-api"])
ACTIONS = ("PUT", "PATCH", "DELETE")


def _err(status: int, text: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status, headers={**(headers or {}), "Cache-Control": "no-store"})


@router.get("/.well-known/gaer-instance-keys")
async def instance_keys():
    """The instance's public keys for export files (Ed25519 signing, X25519 encryption) with fingerprints."""
    from app import db
    from app.devkit import portability
    async with db.connection() as conn:
        return JSONResponse({"keys": await portability.public_keys(conn)}, headers={"Cache-Control": "max-age=300"})


@router.post("/gaer/oauth/token")
async def token(request: Request):
    form = await request.form()
    if form.get("grant_type") != "client_credentials":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    try:
        return JSONResponse(await apis.client_credentials(str(form.get("client_id") or ""), str(form.get("client_secret") or ""),
                                                          str(form.get("scope") or "") or None), headers={"Cache-Control": "no-store"})
    except HTTPException:
        return JSONResponse({"error": "invalid_client"}, status_code=401)


@router.api_route("/gaer/v{major}/{slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def gateway(major: int, slug: str, path: str, request: Request):
    started = time.monotonic()
    found = await apis.line(slug, major)
    if found is None:
        return _err(404, "API'et eller versionen findes ikke")
    a, v, m = found
    d = v["definition"]
    origin = request.headers.get("origin")
    cors = {}
    if origin and origin in d.get("cors", []):
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key, If-None-Match",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Expose-Headers": "ETag, Deprecation, Sunset, Retry-After"}
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors)
    dep = apis.deprecation_headers(m)
    if m["sunset_at"] and m["sunset_at"] <= datetime.now(timezone.utc):
        return _err(410, f"v{major} er lukket. Brug den nyeste major", cors)
    if path in ("openapi.json", "openapi") and request.method == "GET":
        return JSONResponse(apis.openapi(a, v, config.settings.origin.rstrip("/")), headers={**cors, **dep})
    o, path_params, other = apis.match(d, request.method, path)
    if o is None:
        return _err(405 if other else 404, "Metoden findes ikke på stien" if other else "Stien findes ikke", {**cors, **dep})
    if o["method"] in ACTIONS:
        return _err(405, "Handlinger kræver en kode-operation, som Developer ikke har endnu", {**cors, **dep})

    meta = request_meta(request)
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    key = user = None
    if bearer.startswith(("gk_", "gt_")):
        key = await apis.key_by_token(bearer, meta.ip)
        if key is None:
            return _err(401, "Nøglen er ugyldig, udløbet eller tilbagekaldt", {**cors, "WWW-Authenticate": "Bearer"})
        if key["api_artifact_id"] not in (None, a["id"]):
            return _err(403, "Nøglen gælder et andet API", cors)
        if not apis.scope_allows(key["scopes"], o, exact=o["level"] in ("yellow", "red")):
            return _err(403, f"Nøglen er ikke godkendt til {apis.op_key(o)}", cors)
    elif bearer:
        user = await oauth.verify_access_token(bearer)
        if user is None:
            return _err(401, "Ugyldigt token", {**cors, "WWW-Authenticate": "Bearer"})
        if o["level"] != "white" and user.audience != "internal":
            return _err(403, "Eksterne brugere har kun adgang til operationer på hvidt niveau", cors)
        if o["level"] in ("yellow", "red"):
            return _err(403, "Operationen kræver en API-nøgle, der er godkendt til den", cors)
    else:
        return _err(401, "Mangler Authorization: Bearer <nøgle>", {**cors, "WWW-Authenticate": "Bearer"})
    ident = key["key_id"] if key else f"user:{user.user_id}"

    per_key = (d.get("limits") or {}).get("per_key")
    if per_key:
        ok, retry = await cache.take(f"api:{slug}:{ident}:{apis.op_key(o)}", int(per_key))
        if not ok:
            await apis.log(api=slug, major=major, method=o["method"], path=o["path"], key_id=key["key_id"] if key else None,
                           user_id=user.user_id if user else None, status=429, ms=int((time.monotonic() - started) * 1000), cache_status=None)
            return _err(429, f"For mange kald. Prøv igen om {retry} sekunder", {**cors, "Retry-After": str(retry)})

    if request.method == "GET":
        args = dict(request.query_params)
        idem = None
    else:
        try:
            body = await request.json() if (await request.body()) else {}
        except ValueError:
            return _err(400, "Body skal være JSON", cors)
        if not isinstance(body, dict):
            return _err(400, "Body skal være et JSON-objekt", cors)
        args = body
        idem = request.headers.get("idempotency-key")
        if o["level"] in ("yellow", "red") and not idem:
            return _err(428, "Mangler Idempotency-Key", cors)
        if idem:
            stored = await cache.idem_get(f"{slug}:{major}:{ident}:{idem}")
            if stored:
                return JSONResponse(stored["body"], status_code=stored["status"], headers={**cors, **dep, "Idempotent-Replayed": "true"})
    args = {**args, **path_params}
    principal = SimpleNamespace(meta=meta, user_id=user.user_id if user else None)
    status, cache_status = 200, None
    try:
        out = await apis.run(o, args, principal)
        cache_status = (out.get("cache") or {}).get("status") or "miss"
        ttl = out.pop("ttl", 0)
        payload = out
    except HTTPException as e:
        status, payload, ttl = e.status_code, {"error": e.detail}, 0
    body = json.dumps(payload, ensure_ascii=False, default=str).encode()
    # The ETag covers the data, not whether it came from the cache or how old it is.
    tag = apis.etag(json.dumps({k: v for k, v in payload.items() if k != "cache"}, ensure_ascii=False, default=str).encode())
    headers = {**cors, **dep, "ETag": tag, "Cache-Control": f"private, max-age={ttl}" if status == 200 else "no-store"}
    ms = int((time.monotonic() - started) * 1000)
    await apis.log(api=slug, major=major, method=o["method"], path=o["path"], key_id=key["key_id"] if key else None,
                   user_id=user.user_id if user else None, status=status, ms=ms, cache_status=cache_status if status == 200 else None)
    if idem and status < 500:
        await cache.idem_put(f"{slug}:{major}:{ident}:{idem}", status, payload)
    if status == 200 and request.method == "GET" and request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(body, status_code=status, media_type="application/json", headers=headers)
