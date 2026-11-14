"""Login-udbydere: /login's provider buttons, the redirects to and from the providers, the user's own linked logins
(/konto) and /api/admin/login-providers (section /login-providers, role Drift). The rules: app/login_providers.py.

  GET  /api/auth/providers?email=           what /login offers (and whether the e-mail's domain must use a provider)
  GET  /api/auth/providers/{key}/start      → 302 to the provider (binds the flow to this browser)
  GET  /api/auth/oidc/{key}/callback        OpenID Connect / OAuth 2.0 → session → /login (second factor, passkey offer)
  POST /api/auth/saml/{key}/acs             SAML response (HTTP-POST, CSRF-exempt: it comes from the IdP)
  GET  /api/auth/saml/{key}/metadata|cert   what the IdP needs from Gær
"""

import re
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import config, db, jobs, mfa, saml, webhooks
from app import login_providers as lp
from app.logs import RequestMeta, audit, auth_event, changed
from app.routes import auth as auth_routes
from app.security import InvalidEmail, normalize_email
from app.sessions import (Principal, clear_session_cookie, current_user, optional_principal, request_meta,
                          require_admin, require_admin_stepup, revoke_sessions)

router = APIRouter(prefix="/api/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/me/identities", tags=["me"])
admin_router = APIRouter(prefix="/api/admin/login-providers", tags=["admin"], dependencies=[Depends(require_admin)])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _same_origin_path(value: str | None) -> str | None:
    """A path on this site, or None: "//host" and "/\\host" lead elsewhere."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")) or len(value) > 500:
        return None
    if re.search(r"[\x00-\x1f]", value):
        return None
    return value


def _set_flow_cookie(response: Response, value: str, *, saml_flow: bool) -> None:
    s = config.settings
    # A SAML response arrives as a cross-site POST: the cookie must be SameSite=None to come along (https only).
    samesite = "none" if saml_flow and s.secure_cookies else "lax"
    response.set_cookie(lp.FLOW_COOKIE, value, max_age=lp.FLOW_MINUTES * 60, httponly=True, secure=s.secure_cookies,
                        samesite=samesite, path="/api/auth/")


def _clear_flow_cookie(response: Response) -> None:
    response.delete_cookie(lp.FLOW_COOKIE, path="/api/auth/")


def _redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    _clear_flow_cookie(response)
    return response


def _login_target(result: dict, return_to: str | None) -> str:
    nxt = _same_origin_path(return_to) or "/"
    if result["status"] == "mfa_required":
        return "/login?" + urlencode({"sso": "mfa", "next": nxt})
    if result.get("offer_passkey") or result.get("admin_grace_left") is not None:
        grace = result.get("admin_grace_left")
        return "/login?" + urlencode({"sso": "offer", "next": nxt} | ({"grace": grace} if grace is not None else {}))
    return nxt


def _error_target(purpose: str, key: str, code: str, return_to: str | None = None) -> str:
    if purpose == "link":
        return f"/konto?{urlencode({'sso_error': code})}#forbundne-logins"
    if purpose == "test":
        return f"/admin/login-providers/{quote(key)}?test=fail"
    return "/login?" + urlencode({"sso_error": code} | ({"next": return_to} if _same_origin_path(return_to) else {}))


async def _provider_or_404(conn, key: str) -> dict:
    if not lp.valid_key(key):
        raise HTTPException(404, "Udbyderen findes ikke")
    return await lp.get(conn, key)


async def _finish(conn, request: Request, provider: dict, flow: dict | None, identity: lp.Identity,
                  meta: RequestMeta) -> RedirectResponse:
    """The provider said who this is: test, link or log in, by the flow's purpose."""
    purpose = flow["purpose"] if flow else "login"
    key = provider["key"]
    if purpose == "test":
        await lp.record_test(conn, provider, identity, flow["user_id"], meta)
        return _redirect(f"/admin/login-providers/{quote(key)}?test=ok")
    if not provider["enabled"]:
        raise lp.Refused("disabled")
    if purpose == "link":
        principal = await optional_principal(request)
        if principal is None or principal.scope != "full" or principal.user_id != flow["user_id"]:
            raise lp.Refused("expired", "session_changed")
        await lp.link_identity(conn, provider, identity, principal.user_id, meta)
        return _redirect(f"/konto?{urlencode({'linked': key})}#forbundne-logins")
    user = await lp.resolve_user(conn, provider, identity, meta)
    await auth_event(conn, "provider.login", ok=True, meta=meta, user_id=user["id"], contact=identity.email,
                     provider=key, kind=provider["kind"])
    response = _redirect("/")
    # Exactly as after a one-time code: "Kræv passkey", admin grace logins and the passkey offer.
    result = await auth_routes._after_first_factor(conn, response, user, meta, contact=identity.email, via="provider")
    response.headers["location"] = _login_target(result, flow["return_to"] if flow else None)
    await lp.emit_login(provider)
    return response


async def _refused(conn, provider: dict, flow: dict | None, e: lp.Refused, meta: RequestMeta) -> RedirectResponse:
    await conn.rollback()
    if flow or e.flow_id:  # the rollback must not make the flow usable again
        await conn.execute("update login_flows set consumed_at = now() where id = %s", (flow["id"] if flow else e.flow_id,))
    purpose = flow["purpose"] if flow else "login"
    if purpose == "test" and flow:
        await lp.record_test(conn, provider, None, flow["user_id"], meta, error=f"{e.code}: {e.detail}"[:200])
    else:
        await auth_event(conn, "provider.login" if purpose == "login" else "provider.linked", ok=False, meta=meta,
                         user_id=flow["user_id"] if flow else None, provider=provider["key"], reason=e.code,
                         detail=e.detail[:200] or None)
    await conn.commit()
    return _redirect(_error_target(purpose, provider["key"], e.code, flow["return_to"] if flow else None))


# ── /login ──────────────────────────────────────────────────────────────────

@router.get("/providers")
async def providers(email: str | None = Query(default=None, max_length=254)):
    """The buttons /login shows. With an e-mail: also the ones for its domain, and the provider it must use."""
    clean = None
    if email:
        try:
            clean = normalize_email(email)
        except InvalidEmail:
            clean = None
    async with db.connection() as conn:
        return await lp.public_list(conn, clean)


@router.get("/providers/{key}/start")
async def start(key: str, request: Request, next: str | None = Query(default=None, max_length=500)):
    async with db.connection() as conn:
        provider = await _provider_or_404(conn, key)
        if not provider["enabled"]:
            return _redirect(_error_target("login", key, "disabled"))
        try:
            url, browser = await lp.start_flow(conn, provider, "login", return_to=_same_origin_path(next))
        except lp.Refused as e:
            return await _refused(conn, provider, None, e, request_meta(request))
    response = RedirectResponse(url, status_code=302)
    _set_flow_cookie(response, browser, saml_flow=provider["kind"] == "saml")
    return response


@router.get("/oidc/{key}/callback")
async def oidc_callback(key: str, request: Request, code: str | None = Query(default=None, max_length=4000),
                        state: str | None = Query(default=None, max_length=200),
                        error: str | None = Query(default=None, max_length=200)):
    meta = request_meta(request)
    async with db.connection() as conn:
        provider = await _provider_or_404(conn, key)
        if provider["kind"] == "saml":
            raise HTTPException(404)
        flow = None
        try:
            flow = await lp.take_flow(conn, provider, state, request.cookies.get(lp.FLOW_COOKIE))
            if error or not code:
                raise lp.Refused("denied" if error in ("access_denied", "consent_required") else "failed", error or "no code")
            identity = await lp.oidc_callback(conn, provider, flow, code)
            return await _finish(conn, request, provider, flow, identity, meta)
        except lp.Refused as e:
            return await _refused(conn, provider, flow, e, meta)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            return await _refused(conn, provider, flow, lp.Refused("failed", type(e).__name__), meta)


@router.get("/oidc/{key}/logout")
async def oidc_logout(key: str, request: Request, response: Response,
                      principal: Principal | None = Depends(optional_principal)):
    """Front-channel logout from the provider: ends this browser's session here too (if the cookie comes along)."""
    if principal and principal.impersonator_id is None:
        async with db.connection() as conn:
            await revoke_sessions(conn, meta=principal.meta, user_id=principal.user_id, session_id=principal.session_id,
                                  reason=f"provider_logout:{key[:40]}")
        clear_session_cookie(response, False)
    return {"ok": True}


@router.get("/saml/{key}/metadata")
async def saml_metadata(key: str):
    async with db.connection() as conn:
        provider = await _provider_or_404(conn, key)
    if provider["kind"] != "saml" or not provider["sp_cert"]:
        raise HTTPException(404)
    return Response(saml.sp_metadata(provider), media_type="application/samlmetadata+xml",
                    headers={"Content-Disposition": f'inline; filename="gaer-{key}-metadata.xml"'})


@router.get("/saml/{key}/cert")
async def saml_cert(key: str):
    async with db.connection() as conn:
        provider = await _provider_or_404(conn, key)
    if provider["kind"] != "saml" or not provider["sp_cert"]:
        raise HTTPException(404)
    return Response(provider["sp_cert"], media_type="application/x-pem-file",
                    headers={"Content-Disposition": f'attachment; filename="gaer-{key}.crt"'})


@router.post("/saml/{key}/acs")
async def saml_acs(key: str, request: Request, SAMLResponse: str = Form(max_length=saml.MAX_RESPONSE * 2),
                   RelayState: str | None = Form(default=None, max_length=200)):
    meta = request_meta(request)
    async with db.connection() as conn:
        provider = await _provider_or_404(conn, key)
        if provider["kind"] != "saml":
            raise HTTPException(404)
        flow = None
        try:
            if RelayState:
                try:
                    flow = await lp.take_flow(conn, provider, RelayState, request.cookies.get(lp.FLOW_COOKIE))
                except lp.Refused:
                    if not provider["config"].get("allow_idp_initiated"):
                        raise
            if flow is None and not provider["config"].get("allow_idp_initiated"):
                raise lp.Refused("expired", "unsolicited")
            row = await db.fetch_one(conn, "select sp_key_enc from login_providers where id = %s", (provider["id"],))
            key_obj = saml.sp_key(provider["id"], row["sp_key_enc"]) if row["sp_key_enc"] else None
            result = saml.process_response(provider, SAMLResponse, key_obj, flow["request_id"] if flow else None)
            await saml.remember_assertion(conn, provider["id"], result)
            return await _finish(conn, request, provider, flow, result.identity, meta)
        except lp.Refused as e:
            return await _refused(conn, provider, flow, e, meta)


@router.get("/saml/{key}/slo")
async def saml_slo(key: str, request: Request, principal: Principal | None = Depends(optional_principal)):
    """Logout started at the IdP: this browser's session ends here, then /login."""
    response = _redirect("/login")
    if principal and principal.impersonator_id is None:
        async with db.connection() as conn:
            await revoke_sessions(conn, meta=principal.meta, user_id=principal.user_id, session_id=principal.session_id,
                                  reason=f"provider_logout:{key[:40]}")
        clear_session_cookie(response, False)
    return response


# ── /konto: the user's own linked logins ────────────────────────────────────

@me_router.get("")
async def my_identities(principal: Principal = Depends(current_user)):
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """
            select p.key, p.kind, p.name, p.enabled, p.config, i.email, i.created_at, i.last_used_at, i.linked_via
            from login_providers p left join user_identities i on i.provider_id = p.id and i.user_id = %s
            where p.enabled or i.id is not null order by p.kind = 'saml', p.id""", (principal.user_id,))
    out = []
    for r in rows:
        domains = r["config"].get("domains") or []
        available = r["enabled"] and (not domains or lp.domain_matches(principal.email, domains))
        out.append({"key": r["key"], "kind": r["kind"], "name": r["name"], "enabled": r["enabled"],
                    "domains": domains, "available": available,
                    "linked": {"email": r["email"], "created_at": r["created_at"], "last_used_at": r["last_used_at"],
                               "via": r["linked_via"]} if r["linked_via"] else None})
    return {"providers": out}


@me_router.post("/{key}/link")
async def link_start(key: str, response: Response, principal: Principal = Depends(current_user)):
    """Starts connecting an account. Needs the same proof as changing a factor (app.mfa.require_recent_mfa)."""
    if principal.impersonator_id is not None:
        raise HTTPException(403, "Ikke muligt for en testkonto")
    async with db.connection() as conn:
        await mfa.require_recent_mfa(conn, principal)
        provider = await _provider_or_404(conn, key)
        if not provider["enabled"]:
            raise HTTPException(409, "Udbyderen er slået fra")
        domains = provider["config"].get("domains") or []
        if domains and not lp.domain_matches(principal.email, domains):
            raise HTTPException(409, "Udbyderen er kun for e-mails fra " + ", ".join(domains))
        try:
            url, browser = await lp.start_flow(conn, provider, "link", user_id=principal.user_id)
        except lp.Refused as e:
            raise HTTPException(502, lp.REFUSALS.get(e.code, lp.REFUSALS["failed"]))
    _set_flow_cookie(response, browser, saml_flow=provider["kind"] == "saml")
    return {"url": url}


@me_router.delete("/{key}")
async def unlink(key: str, principal: Principal = Depends(current_user)):
    if principal.impersonator_id is not None:
        raise HTTPException(403, "Ikke muligt for en testkonto")
    async with db.connection() as conn:
        await mfa.require_recent_mfa(conn, principal)
        provider = await _provider_or_404(conn, key)
        row = await db.fetch_one(conn, """delete from user_identities where provider_id = %s and user_id = %s
                                          returning id, linked_via""", (provider["id"], principal.user_id))
        if row is None:
            raise HTTPException(404, "Kontoen er ikke forbundet")
        await audit(conn, "delete", actor_id=principal.user_id, meta=principal.meta, table="user_identities",
                    row_id=row["id"], before={"provider": key, "user_id": principal.user_id, "linked_via": row["linked_via"]})
        await auth_event(conn, "provider.unlinked", ok=True, meta=principal.meta, user_id=principal.user_id, provider=key)
    return {"ok": True}


# ── /admin/login-providers ──────────────────────────────────────────────────

def _summary(p: dict, linked: int) -> dict:
    k = lp.KINDS[p["kind"]]
    miss = lp.missing(p)
    return {"key": p["key"], "kind": p["kind"], "name": p["name"], "label": lp.label(p), "enabled": p["enabled"],
            "protocol": k.protocol, "subtitle": k.subtitle, "audience": k.audience,
            "status": "active" if p["enabled"] else ("incomplete" if miss else "ready"), "missing": miss,
            "domains": p["config"].get("domains") or [], "new_users": p["config"].get("new_users", "existing"),
            "only_matching_domain": bool(p["config"].get("only_matching_domain")),
            "linked": linked, "cert_days": lp.cert_days(p) if p["kind"] == "saml" else None,
            "last_login_at": p["last_login_at"], "enabled_at": p["enabled_at"], "updated_at": p["updated_at"]}


EVENT_ACTIONS = ("login_provider.created", "login_provider.updated", "login_provider.enabled", "login_provider.disabled",
                 "login_provider.tested", "login_provider.deleted", "login_provider.metadata", "login_provider.rules",
                 "login_provider.cert_expiring")


@admin_router.get("")
async def overview():
    async with db.connection() as conn:
        rows = await lp.all_providers(conn)
        counts = {r["provider_id"]: r["n"] for r in await db.fetch_all(
            conn, "select provider_id, count(*) as n from user_identities group by provider_id")}
        events = await db.fetch_all(conn, """
            select a.ts, a.action, a.row_id, a.detail, p.name as provider_name, p.kind
            from audit_log a left join login_providers p on p.id::text = a.row_id and a.table_name = 'login_providers'
            where a.action = any(%s) order by a.ts desc limit 8""", (list(EVENT_ACTIONS),))
        rules = await lp.get_rules(conn)
    have = {r["kind"] for r in rows}
    catalog = [{"kind": k.key, "label": k.label, "subtitle": k.subtitle, "audience": k.audience, "protocol": k.protocol,
                "set_up": k.key in have} for k in lp.KINDS.values()]
    return {"providers": [_summary(r, counts.get(r["id"], 0)) for r in rows], "catalog": catalog, "rules": rules,
            "events": events, "origin": lp.origin()}


class RulesIn(BaseModel):
    link: str = Field(default="verified_email", max_length=40)
    new_users: str = Field(default="signup", max_length=40)
    required: list[dict] = Field(default_factory=list, max_length=50)


@admin_router.put("/rules")
async def put_rules(body: RulesIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        keys = {r["key"] for r in await lp.all_providers(conn)}
        new = lp.clean_rules(body.model_dump(), keys)
        old = await lp.get_rules(conn)
        await conn.execute("""insert into settings (key, value, updated_at) values ('login_providers', %s, now())
                              on conflict (key) do update set value = excluded.value, updated_at = now()""", (Jsonb(new),))
        before, after = changed(old, new)
        await audit(conn, "login_provider.rules", actor_id=admin.user_id, meta=admin.meta, table="settings",
                    row_id="login_providers", before=before, after=after)
    return new


class CreateIn(BaseModel):
    kind: str = Field(max_length=20)
    name: str | None = Field(default=None, max_length=80)


@admin_router.post("")
async def create(body: CreateIn, admin: Principal = Depends(require_admin_stepup)):
    kind = lp.KINDS.get(body.kind)
    if kind is None:
        raise HTTPException(422, "Ukendt udbyder")
    name = (body.name or "").strip() or kind.label
    async with db.connection() as conn:
        if kind.key == "saml":
            from app.groups import slugify
            base = slugify(name)
            base = base if base[:1].isalpha() else f"saml-{base}"
            key, n = base[:36], 2
            while await db.fetch_one(conn, "select 1 from login_providers where key = %s", (key,)):
                key, n = f"{base[:33]}-{n}", n + 1
        else:
            key = kind.key
            if await db.fetch_one(conn, "select 1 from login_providers where kind = %s", (kind.key,)):
                raise HTTPException(409, f"{kind.label} er allerede sat op")
        cfg = lp.clean_config(kind.key, {})
        row = await db.fetch_one(conn, """insert into login_providers (key, kind, name, config, created_by)
                                          values (%s, %s, %s, %s, %s) returning id""",
                                 (key, kind.key, name, Jsonb(cfg), admin.user_id))
        if kind.key == "saml":
            key_pem, cert_pem = saml.generate_sp_keypair(f"{lp.origin()} {key}")
            await conn.execute("update login_providers set sp_key_enc = %s, sp_cert = %s where id = %s",
                               (saml.encrypt_sp_key(row["id"], key_pem), cert_pem, row["id"]))
        await audit(conn, "login_provider.created", actor_id=admin.user_id, meta=admin.meta, table="login_providers",
                    row_id=row["id"], after={"key": key, "kind": kind.key, "name": name})
        await webhooks.emit(conn, "auth.provider.updated", {"provider_id": row["id"], "kind": kind.key})
    return {"key": key}


@admin_router.get("/{key}")
async def detail(key: str):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        linked = await lp.linked_count(conn, p["id"])
        stats = await db.fetch_one(conn, """
            select count(*) filter (where result = 'fail') as errors_24h, max(ts) filter (where result = 'ok') as last_ok
            from auth_log where event = 'provider.login' and detail->>'provider' = %s and ts > now() - interval '24 hours'""",
                                   (key,))
        group_rows = await db.fetch_all(conn, "select id, name, slug from groups order by name")
    out = _summary(p, linked)
    meta = p["idp_meta"] or {}
    out.update({
        "config": p["config"], "urls": lp.urls(p), "secret_set": p["secret_set"],
        "client_secret_set_at": p["client_secret_set_at"], "last_test": p["last_test"],
        "idp": {"entity_id": meta.get("entity_id"), "sso_url": meta.get("sso_url"), "slo_url": meta.get("slo_url"),
                "fetched_at": meta.get("fetched_at"), "error": meta.get("error"),
                "certs": [{"sha256": c["sha256"], "not_after": c["not_after"]} for c in meta.get("certs") or []]}
               if p["kind"] == "saml" else None,
        "stats": {"errors_24h": stats["errors_24h"], "last_login_at": p["last_login_at"]},
        "groups": group_rows, "can_map_groups": lp.KINDS[p["kind"]].groups,
        "default_button": lp.KINDS[p["kind"]].button,
    })
    return out


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    config: dict
    client_secret: str | None = Field(default=None, max_length=2000)   # write-only; None = unchanged
    # SAML entered by hand (metadata_source = manual)
    idp_entity_id: str | None = Field(default=None, max_length=500)
    idp_sso_url: str | None = Field(default=None, max_length=2000)
    idp_cert: str | None = Field(default=None, max_length=20000)


@admin_router.put("/{key}")
async def update(key: str, body: ProviderIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        cfg = lp.clean_config(p["kind"], body.config)
        if lp.KINDS[p["kind"]].groups and cfg.get("group_map"):
            ids = {g["group_id"] for g in cfg["group_map"]}
            found = {r["id"] for r in await db.fetch_all(conn, "select id from groups where id = any(%s)", (list(ids),))}
            if ids - found:
                raise HTTPException(422, "En gruppe i oversættelsen findes ikke")
        sets, params = ["name = %s", "config = %s", "updated_at = now()"], [body.name.strip(), Jsonb(cfg)]
        secret_changed = False
        if body.client_secret is not None and body.client_secret.strip() and lp.KINDS[p["kind"]].protocol != "saml":
            sets += ["client_secret_enc = %s", "client_secret_set_at = now()"]
            params.append(lp.encrypt_client_secret(p["id"], body.client_secret.strip()))
            secret_changed = True
        if p["kind"] == "saml" and cfg["metadata_source"] == "manual" and (body.idp_sso_url or body.idp_entity_id):
            try:
                webhooks.check_url(body.idp_sso_url or "")
            except webhooks.UnsafeUrl as e:
                raise HTTPException(422, f"SSO-adresse: {e}")
            try:
                meta = saml.manual_metadata(body.idp_entity_id or "", body.idp_sso_url or "", body.idp_cert or "")
            except lp.Refused as e:
                raise HTTPException(422, "Certifikatet kunne ikke læses" if e.detail == "certificate" else e.detail)
            if not meta["certs"] and p["idp_meta"]:
                meta["certs"] = p["idp_meta"].get("certs") or []
            sets.append("idp_meta = %s")
            params.append(Jsonb(meta | {"fetched_at": None, "source": "manual"}))
        params.append(p["id"])
        await conn.execute(f"update login_providers set {', '.join(sets)} where id = %s", params)
        before, after = changed({"name": p["name"], **p["config"]}, {"name": body.name.strip(), **cfg})
        await audit(conn, "login_provider.updated", actor_id=admin.user_id, meta=admin.meta, table="login_providers",
                    row_id=p["id"], before=before, after=after, **({"client_secret_changed": True} if secret_changed else {}))
        await webhooks.emit(conn, "auth.provider.updated", {"provider_id": p["id"], "kind": p["kind"]})
        if p["enabled"]:
            fresh = await lp.get(conn, key)
            if lp.missing(fresh):
                raise HTTPException(422, "Udbyderen er slået til og mangler nu: " + ", ".join(lp.missing(fresh)))
    return await detail(key)


class EnabledIn(BaseModel):
    enabled: bool


@admin_router.post("/{key}/enabled")
async def set_enabled(key: str, body: EnabledIn, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        if body.enabled and (miss := lp.missing(p)):
            raise HTTPException(422, "Mangler: " + ", ".join(miss))
        if body.enabled == p["enabled"]:
            return {"enabled": p["enabled"]}
        await conn.execute("""update login_providers set enabled = %s, updated_at = now(),
                              enabled_at = case when %s then now() else enabled_at end,
                              enabled_by = case when %s then %s else enabled_by end where id = %s""",
                           (body.enabled, body.enabled, body.enabled, admin.user_id, p["id"]))
        action = "login_provider.enabled" if body.enabled else "login_provider.disabled"
        await audit(conn, action, actor_id=admin.user_id, meta=admin.meta, table="login_providers", row_id=p["id"],
                    before={"enabled": p["enabled"]}, after={"enabled": body.enabled},
                    mfa="yubikey" if admin.yubikey_required else "passkey")
        await webhooks.emit(conn, "auth.provider.enabled" if body.enabled else "auth.provider.disabled",
                            {"provider_id": p["id"], "kind": p["kind"]})
    return {"enabled": body.enabled}


@admin_router.post("/{key}/test")
async def test_login(key: str, response: Response, admin: Principal = Depends(require_admin_stepup)):
    """Runs a login at the provider that logs nobody in: the result (claim names, whether the e-mail is verified) is
    stored on the provider."""
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        if miss := lp.missing(p):
            raise HTTPException(422, "Mangler: " + ", ".join(miss))
        try:
            url, browser = await lp.start_flow(conn, p, "test", user_id=admin.user_id)
        except lp.Refused as e:
            raise HTTPException(502, f"Udbyderen svarede ikke som ventet ({e.detail[:120]})")
    _set_flow_cookie(response, browser, saml_flow=p["kind"] == "saml")
    return {"url": url}


class MetadataIn(BaseModel):
    xml: str | None = Field(default=None, max_length=1_000_000)


@admin_router.post("/{key}/metadata")
async def refresh_metadata(key: str, body: MetadataIn, admin: Principal = Depends(require_admin_stepup)):
    """SAML: reads the IdP's metadata now, from its URL or from an uploaded XML file."""
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        if p["kind"] != "saml":
            raise HTTPException(404)
        try:
            if body.xml:
                meta = saml.parse_metadata(body.xml.encode())
                source = "xml"
            else:
                if not p["config"].get("metadata_url"):
                    raise HTTPException(422, "Skriv metadata-URL'en først")
                meta = saml.parse_metadata(await saml.fetch_metadata(p["config"]["metadata_url"]))
                source = "url"
        except lp.Refused as e:
            raise HTTPException(422, f"Metadata kunne ikke læses: {e.detail}")
        meta |= {"fetched_at": lp._now().isoformat(), "source": source}
        await conn.execute("update login_providers set idp_meta = %s, updated_at = now() where id = %s", (Jsonb(meta), p["id"]))
        await audit(conn, "login_provider.metadata", actor_id=admin.user_id, meta=admin.meta, table="login_providers",
                    row_id=p["id"], entity_id=meta["entity_id"], certs=[c["sha256"] for c in meta["certs"]], source=source)
    return await detail(key)


@admin_router.get("/{key}/users")
async def linked_users(key: str):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        rows = await db.fetch_all(conn, """
            select u.id, u.email, u.display_name, u.role, u.blocked_at, i.email as provider_email, i.linked_via,
                   i.created_at, i.last_used_at
            from user_identities i join users u on u.id = i.user_id where i.provider_id = %s
            order by i.last_used_at desc nulls last, i.created_at desc limit 500""", (p["id"],))
    return {"users": rows}


@admin_router.delete("/{key}/users/{user_id}")
async def admin_unlink(key: str, user_id: int, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        row = await db.fetch_one(conn, """delete from user_identities where provider_id = %s and user_id = %s
                                          returning id, linked_via""", (p["id"], user_id))
        if row is None:
            raise HTTPException(404, "Brugeren er ikke koblet til udbyderen")
        await audit(conn, "delete", actor_id=admin.user_id, meta=admin.meta, table="user_identities", row_id=row["id"],
                    before={"provider": key, "user_id": user_id, "linked_via": row["linked_via"]})
    return {"ok": True}


@admin_router.get("/{key}/log")
async def provider_log(key: str):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        auth = await db.fetch_all(conn, """
            select ts, event, result, user_id, phone_masked as contact, device, detail->>'reason' as reason
            from auth_log where event like 'provider.%%' and detail->>'provider' = %s order by ts desc limit 100""", (key,))
        changes = await db.fetch_all(conn, """
            select ts, action, actor_user_id, detail, before, after from audit_log
            where table_name = 'login_providers' and row_id = %s order by ts desc limit 50""", (str(p["id"]),))
    return {"auth": auth, "changes": changes}


@admin_router.delete("/{key}")
async def delete_provider(key: str, admin: Principal = Depends(require_admin_stepup)):
    async with db.connection() as conn:
        p = await _provider_or_404(conn, key)
        if p["enabled"]:
            raise HTTPException(409, "Slå udbyderen fra først")
        rules = await lp.get_rules(conn)
        if any(r["provider"] == key for r in rules["required"]):
            raise HTTPException(409, "Et domæne kræver denne udbyder. Fjern reglen først.")
        linked = await lp.linked_count(conn, p["id"])
        await conn.execute("delete from login_providers where id = %s", (p["id"],))
        await audit(conn, "login_provider.deleted", actor_id=admin.user_id, meta=admin.meta, table="login_providers",
                    row_id=p["id"], before={"key": key, "kind": p["kind"], "name": p["name"]}, linked_users=linked)
        await webhooks.emit(conn, "auth.provider.updated", {"provider_id": p["id"], "kind": p["kind"], "deleted": True})
    return {"ok": True}


# ── Job: SAML metadata and certificates ─────────────────────────────────────

@jobs.handler("auth.saml_metadata")
async def saml_metadata_job(payload: dict, job: dict) -> dict:
    """Refreshes the metadata of SAML connections that fetch it from a URL, and warns (audit + webhook) when an IdP
    signing certificate expires within CERT_WARN_DAYS."""
    refreshed, failed, warned = 0, 0, 0
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"select {lp.COLUMNS} from login_providers where kind = 'saml'")
    for p in rows:
        cfg = p["config"]
        if cfg.get("metadata_source") == "url" and cfg.get("refresh", True) and cfg.get("metadata_url"):
            try:
                meta = saml.parse_metadata(await saml.fetch_metadata(cfg["metadata_url"]))
                meta |= {"fetched_at": lp._now().isoformat(), "source": "url"}
                async with db.connection() as conn:
                    old = p["idp_meta"] or {}
                    await conn.execute("update login_providers set idp_meta = %s where id = %s", (Jsonb(meta), p["id"]))
                    if [c["sha256"] for c in old.get("certs") or []] != [c["sha256"] for c in meta["certs"]]:
                        await audit(conn, "login_provider.metadata", actor_id=None, meta=RequestMeta(),
                                    table="login_providers", row_id=p["id"], entity_id=meta["entity_id"],
                                    certs=[c["sha256"] for c in meta["certs"]], source="job")
                p = {**p, "idp_meta": meta}
                refreshed += 1
            except (lp.Refused, httpx.HTTPError):
                failed += 1
                async with db.connection() as conn:
                    await conn.execute("""update login_providers set idp_meta = coalesce(idp_meta, '{}'::jsonb) || %s
                                          where id = %s""", (Jsonb({"error": "Metadata kunne ikke hentes"}), p["id"]))
        days = lp.cert_days(p)
        if p["enabled"] and days is not None and days <= lp.CERT_WARN_DAYS:
            async with db.connection() as conn:
                await audit(conn, "login_provider.cert_expiring", actor_id=None, meta=RequestMeta(),
                            table="login_providers", row_id=p["id"], days=days)
                await webhooks.emit(conn, "auth.provider.cert_expiring", {"provider_id": p["id"], "days": days})
            warned += 1
    return {"refreshed": refreshed, "failed": failed, "warned": warned}
