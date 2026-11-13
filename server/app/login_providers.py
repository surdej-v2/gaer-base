"""Login-udbydere: log ind med en konto, brugeren allerede har (/admin/login-providers).

Providers: Microsoft Entra ID and Google (OpenID Connect, authorization code + PKCE), GitHub and Amazon (OAuth 2.0)
and any number of SAML 2.0 connections (app/saml.py). Each is off until an admin switches it on.

The rules (also in the gaer-auth skill):

- **A provider replaces only the one-time code.** After it, everything is as after a code (routes/auth.py
  `_after_first_factor`): "Kræv passkey" gives a pending session, /admin needs a second factor after the grace logins,
  security actions need a step-up. A provider never counts as a second factor and never makes anyone admin.
- **Which account.** An identity (the provider's stable subject) is linked to one user. The first time, it is linked
  on an e-mail the provider has verified (rule `link` = "verified_email"), or only when the user linked it themselves
  from /konto ("user_linked"). Entra's e-mail counts as verified only on a domain the admin listed for the provider;
  Amazon's never does.
- **New people.** Only when both the provider and the rules allow it, the e-mail is verified, and Tilmelding
  (app/signup.py) allows the address. The role is always `user`.
- **Allowed domains.** A provider with domains only accepts e-mails on them (subdomains included).
- **Required provider for a domain** (rule `required`): /login sends that domain to the provider and no one-time code
  is sent to it, except to the super admin (emergency access).
- **Flows** are bound to the browser that started them (cookie, hash in login_flows), single use and expire after
  FLOW_MINUTES. PKCE and nonce for OIDC; SAML checks InResponseTo, audience, destination and replays.
- Client secrets and SAML SP keys are encrypted (app.secrets_box) and never returned, logged or sent in webhooks.
- Changing a provider or the rules needs a step-up and is audited; webhooks carry ids only.
"""

import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import access, config, db, events, integrations, secrets_box, settings_store, signup, webhooks
from app.logs import RequestMeta, audit, auth_event
from app.security import token_hash

FLOW_MINUTES = 10
FLOW_COOKIE = "gaer_login_flow"
CERT_WARN_DAYS = 30
SCOPES = "openid email profile"
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_KEY = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    protocol: str            # oidc, oauth2, saml
    subtitle: str
    audience: str            # who typically uses it (shown in the overview)
    button: str              # default text of the button on /login
    groups: bool = False     # can map the provider's groups to Gær groups


KINDS: dict[str, Kind] = {
    "entra": Kind("entra", "Microsoft Entra ID", "oidc", "OpenID Connect · én tenant",
                  "Ansatte i jeres Microsoft 365-tenant", "Fortsæt med Microsoft", groups=True),
    "google": Kind("google", "Google", "oidc", "OpenID Connect · Workspace",
                   "Et Google Workspace-domæne eller Google-konti", "Fortsæt med Google"),
    "github": Kind("github", "GitHub", "oauth2", "OAuth 2.0 · GitHub App",
                   "Typisk: udviklere med en GitHub-konto", "Fortsæt med GitHub"),
    "amazon": Kind("amazon", "Amazon", "oauth2", "Login with Amazon · OAuth 2.0",
                   "Private Amazon-konti · e-mail skal være bekræftet", "Fortsæt med Amazon"),
    "saml": Kind("saml", "SAML 2.0", "saml", "SAML 2.0 · fx ADFS, Okta, Entra",
                 "En organisation med egen IdP", "Log ind med virksomhedens SSO", groups=True),
}

# OAuth 2.0 providers without OpenID Connect: fixed endpoints.
OAUTH = {
    "github": {"authorize": "https://github.com/login/oauth/authorize", "token": "https://github.com/login/oauth/access_token",
               "scope": "read:user user:email"},
    "amazon": {"authorize": "https://www.amazon.com/ap/oa", "token": "https://api.amazon.com/auth/o2/token",
               "scope": "profile"},
}
GOOGLE_ISSUER = "https://accounts.google.com"
ENTRA_AUTHORITY = "https://login.microsoftonline.com"

RULES_DEFAULT = {"link": "verified_email", "new_users": "signup", "required": []}
LINK_MODES = ("verified_email", "user_linked")
NEW_USER_MODES = ("signup", "never")

# Why a login was refused → the text /login shows (?sso_error=<code>). The auth log has the reason too.
REFUSALS = {
    "no_account": "Du har ikke en konto her. Kontakt en administrator.",
    "blocked": "Din bruger er spærret. Kontakt en administrator.",
    "domain": "Din e-mail hører ikke til et domæne, der må bruge denne udbyder.",
    "unverified": "Udbyderen har ikke bekræftet din e-mail. Log ind med en kode, og forbind kontoen under Konto.",
    "other_account": "Din konto er allerede forbundet med en anden konto hos udbyderen.",
    "taken": "Kontoen hos udbyderen er allerede forbundet med en anden bruger.",
    "expired": "Login tog for lang tid eller blev startet i en anden browser. Prøv igen.",
    "disabled": "Udbyderen er slået fra.",
    "failed": "Login hos udbyderen mislykkedes. Prøv igen, eller log ind med en kode.",
    "denied": "Du afbrød login hos udbyderen.",
    "test_account": "Testkonti kan ikke logge ind.",
}


class Refused(Exception):
    """A login, link or test that cannot go on. `code` is a key of REFUSALS; `detail` goes to the auth log only."""

    def __init__(self, code: str, detail: str = "", flow_id: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.flow_id = flow_id   # a flow that was used up before the refusal (stays used after the rollback)


@dataclass
class Identity:
    subject: str
    email: str | None
    email_verified: bool
    name: str | None = None
    groups: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)   # names only, for the test result
    tenant: str | None = None


# ── Settings ────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def origin() -> str:
    return config.settings.origin.rstrip("/")


def clean_domains(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for d in raw or []:
        d = str(d).strip().lower().lstrip("@").rstrip(".")
        if not d:
            continue
        if not _DOMAIN.match(d):
            raise HTTPException(422, f"Ugyldigt domæne: {d[:80]}")
        if d not in out:
            out.append(d)
    if len(out) > 50:
        raise HTTPException(422, "Højst 50 domæner")
    return out


def domain_of(email: str | None) -> str:
    return (email or "").rsplit("@", 1)[-1].lower() if email and "@" in email else ""


def domain_matches(email: str | None, domains: list[str]) -> bool:
    d = domain_of(email)
    return bool(d) and any(d == x or d.endswith("." + x) for x in domains)


def _text(value, limit: int, default: str = "") -> str:
    return str(value if value is not None else default).strip()[:limit]


def clean_config(kind: str, raw: dict, existing: dict | None = None) -> dict:
    """The provider's non-secret settings, validated. Unknown keys are dropped. Raises 422 with a Danish message."""
    k = KINDS[kind]
    cfg: dict = {
        "domains": clean_domains(raw.get("domains")),
        "new_users": raw.get("new_users", "existing"),
        "button_label": _text(raw.get("button_label"), 60) or k.button,
        "only_matching_domain": bool(raw.get("only_matching_domain", kind == "saml")),
    }
    if cfg["new_users"] not in ("existing", "signup"):
        raise HTTPException(422, "Ukendt valg for nye brugere")
    if k.protocol in ("oidc", "oauth2"):
        cfg["client_id"] = _text(raw.get("client_id"), 200)
    if kind == "entra":
        cfg["tenant_mode"] = raw.get("tenant_mode", "single")
        if cfg["tenant_mode"] not in ("single", "multi"):
            raise HTTPException(422, "Ukendt valg for tenants")
        cfg["tenant_id"] = _text(raw.get("tenant_id"), 100).lower()
        tenants = [_text(t, 36).lower() for t in raw.get("tenants") or [] if _text(t, 36)]
        if any(not _GUID.match(t) for t in tenants):
            raise HTTPException(422, "Tenant-id'er skal være GUID'er")
        cfg["tenants"] = tenants[:20]
        if cfg["tenant_id"] and not (_GUID.match(cfg["tenant_id"]) or _DOMAIN.match(cfg["tenant_id"])):
            raise HTTPException(422, "Tenant-id skal være et GUID eller et domæne som firma.onmicrosoft.com")
    if kind == "google":
        hd = _text(raw.get("hosted_domain"), 253).lower()
        if hd and not _DOMAIN.match(hd):
            raise HTTPException(422, "Ugyldigt Workspace-domæne")
        cfg["hosted_domain"] = hd
    if k.groups:
        mapping = []
        for m in raw.get("group_map") or []:
            claim = _text(m.get("claim"), 200)
            try:
                gid = int(m.get("group_id"))
            except (TypeError, ValueError):
                gid = 0
            if claim and gid > 0:
                mapping.append({"claim": claim, "group_id": gid})
        cfg["group_map"] = mapping[:50]
    if kind == "saml":
        source = raw.get("metadata_source", "url")
        if source not in ("url", "xml", "manual"):
            raise HTTPException(422, "Ukendt kilde til metadata")
        cfg.update({
            "metadata_source": source,
            "metadata_url": _text(raw.get("metadata_url"), 2000),
            "refresh": bool(raw.get("refresh", True)),
            "attr_email": _text(raw.get("attr_email"), 300) or "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
            "attr_name": _text(raw.get("attr_name"), 300),
            "attr_groups": _text(raw.get("attr_groups"), 300),
            "nameid_format": raw.get("nameid_format", "persistent"),
            "want_encrypted": bool(raw.get("want_encrypted", False)),
            "allow_idp_initiated": bool(raw.get("allow_idp_initiated", False)),
        })
        if cfg["nameid_format"] not in ("persistent", "email"):
            raise HTTPException(422, "Ukendt NameID-format")
        if cfg["metadata_url"]:
            try:
                webhooks.check_url(cfg["metadata_url"])
            except webhooks.UnsafeUrl as e:
                raise HTTPException(422, f"Metadata-URL: {e}")
    return cfg


async def get_rules(conn) -> dict:
    value = await settings_store.get(conn, "login_providers")
    return {**RULES_DEFAULT, **(value or {})}


def clean_rules(raw: dict, provider_keys: set[str]) -> dict:
    link = raw.get("link", "verified_email")
    new_users = raw.get("new_users", "signup")
    if link not in LINK_MODES or new_users not in NEW_USER_MODES:
        raise HTTPException(422, "Ukendt regel")
    required = []
    seen = set()
    for r in raw.get("required") or []:
        d = clean_domains([r.get("domain", "")])
        p = str(r.get("provider", ""))
        if not d:
            continue
        if p not in provider_keys:
            raise HTTPException(422, f"Ukendt udbyder for {d[0]}")
        if d[0] in seen:
            raise HTTPException(422, f"{d[0]} står der to gange")
        seen.add(d[0])
        required.append({"domain": d[0], "provider": p})
    return {"link": link, "new_users": new_users, "required": required[:50]}


# ── Providers ───────────────────────────────────────────────────────────────

COLUMNS = """id, key, kind, name, enabled, config, client_secret_set_at, sp_cert, idp_meta, last_test, last_login_at,
             created_by, created_at, updated_at, enabled_at, enabled_by, client_secret_enc is not null as secret_set"""


async def get(conn, key: str) -> dict:
    row = await db.fetch_one(conn, f"select {COLUMNS} from login_providers where key = %s", (key,))
    if row is None:
        raise HTTPException(404, "Udbyderen findes ikke")
    return row


async def all_providers(conn) -> list[dict]:
    return await db.fetch_all(conn, f"select {COLUMNS} from login_providers order by kind = 'saml', id")


def label(provider: dict) -> str:
    return provider["config"].get("button_label") or KINDS[provider["kind"]].button


def urls(provider: dict) -> dict:
    o, key = origin(), provider["key"]
    if provider["kind"] == "saml":
        return {"entity_id": f"{o}/api/auth/saml/{key}", "acs": f"{o}/api/auth/saml/{key}/acs",
                "slo": f"{o}/api/auth/saml/{key}/slo", "metadata": f"{o}/api/auth/saml/{key}/metadata",
                "cert": f"{o}/api/auth/saml/{key}/cert"}
    return {"redirect_uri": f"{o}/api/auth/oidc/{key}/callback", "logout": f"{o}/api/auth/oidc/{key}/logout"}


def missing(provider: dict) -> list[str]:
    """What is still needed before the provider can be switched on (Danish, for the UI)."""
    cfg, kind = provider["config"], provider["kind"]
    out = ["mindst ét domæne (knappen vises kun for e-mails fra jeres domæner)"] \
        if cfg.get("only_matching_domain") and not cfg.get("domains") else []
    if kind == "saml":
        meta = provider.get("idp_meta") or {}
        if not meta.get("sso_url"):
            out.append("IdP'ens SSO-adresse")
        if not meta.get("entity_id"):
            out.append("IdP'ens Entity ID")
        if not meta.get("certs"):
            out.append("IdP'ens signeringscertifikat")
        if not cfg.get("attr_email") and cfg.get("nameid_format") != "email":
            out.append("attributten for e-mail")
        return out
    if not cfg.get("client_id"):
        out.append("klient-id")
    if not provider.get("secret_set"):
        out.append("klienthemmelighed")
    if kind == "entra":
        if cfg.get("tenant_mode") == "single" and not cfg.get("tenant_id"):
            out.append("tenant-id")
        if cfg.get("tenant_mode") == "multi" and not cfg.get("tenants"):
            out.append("mindst én tenant")
    return out


def cert_days(provider: dict) -> int | None:
    """Days until the IdP's soonest signing certificate expires (SAML)."""
    certs = (provider.get("idp_meta") or {}).get("certs") or []
    ends = [datetime.fromisoformat(c["not_after"]) for c in certs if c.get("not_after")]
    if not ends:
        return None
    return (min(ends) - _now()).days


def client_secret(provider_id: int, blob) -> str:
    return secrets_box.decrypt(blob, f"login-provider:{provider_id}")


def encrypt_client_secret(provider_id: int, value: str) -> bytes:
    return secrets_box.encrypt(value, f"login-provider:{provider_id}")


async def public_list(conn, email: str | None) -> dict:
    """What /login offers: the enabled providers whose button shows for this e-mail (or for no e-mail yet), and the
    provider a domain must use. Reveals nothing about accounts, only the instance's configuration per domain."""
    rows = await db.fetch_all(conn, "select key, kind, name, config from login_providers where enabled order by kind = 'saml', id")
    rules = await get_rules(conn)
    shown, sso = [], False
    for r in rows:
        cfg = r["config"]
        matches = domain_matches(email, cfg.get("domains") or [])
        if cfg.get("only_matching_domain"):
            sso = True
            if not matches:
                continue
        elif email and cfg.get("domains") and not matches:
            continue
        shown.append({"key": r["key"], "kind": r["kind"], "label": label(r), "name": r["name"]})
    required = None
    if email:
        for req in rules["required"]:
            if domain_matches(email, [req["domain"]]):
                p = next((r for r in rows if r["key"] == req["provider"]), None)
                if p:
                    required = {"key": p["key"], "kind": p["kind"], "label": label(p), "name": p["name"],
                                "domain": req["domain"]}
                break
    return {"providers": shown, "required": required, "sso": sso}


async def required_for(conn, email: str) -> dict | None:
    """The enabled provider an e-mail's domain must use (rule `required`), or None."""
    rules = await get_rules(conn)
    for req in rules["required"]:
        if domain_matches(email, [req["domain"]]):
            return await db.fetch_one(conn, "select id, key, name from login_providers where key = %s and enabled",
                                      (req["provider"],))
    return None


# ── Flows ───────────────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def start_flow(conn, provider: dict, purpose: str, *, user_id: int | None = None,
                     return_to: str | None = None) -> tuple[str, str]:
    """Stores a new flow and returns (the address to send the browser to, the cookie value that binds it)."""
    browser = secrets.token_urlsafe(32)
    flow_id = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(24)
    request_id = "_" + secrets.token_hex(20)
    await conn.execute("""insert into login_flows (id, provider_id, purpose, user_id, browser_hash, verifier, nonce,
                                                   request_id, return_to, expires_at)
                          values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s))""",
                       (token_hash(flow_id), provider["id"], purpose, user_id, token_hash(browser), verifier, nonce,
                        request_id, return_to, FLOW_MINUTES))
    if provider["kind"] == "saml":
        from app import saml
        url = saml.authn_request_url(provider, request_id=request_id, relay_state=flow_id)
    else:
        url = await authorize_url(provider, state=flow_id, nonce=nonce, verifier=verifier)
    return url, browser


async def take_flow(conn, provider: dict, flow_id: str | None, browser: str | None) -> dict:
    """The flow a callback belongs to, consumed. It must have been started in this browser and not be used or old."""
    if not flow_id:
        raise Refused("expired", "no_state")
    flow = await db.fetch_one(conn, """
        update login_flows set consumed_at = now()
        where id = %s and provider_id = %s and consumed_at is null and expires_at > now()
        returning id, purpose, user_id, browser_hash, verifier, nonce, request_id, return_to""",
        (token_hash(flow_id), provider["id"]))
    if flow is None:
        raise Refused("expired", "unknown_or_used_state")
    if not browser or not secrets.compare_digest(flow["browser_hash"], token_hash(browser)):
        raise Refused("expired", "other_browser", flow_id=flow["id"])
    return flow


# ── OpenID Connect and OAuth 2.0 ────────────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}
CACHE_SECONDS = 3600


def http_client() -> httpx.AsyncClient:
    """Calls to the providers (tests replace it). Recorded as api.call like every outside API (app.integrations)."""
    return httpx.AsyncClient(timeout=10, event_hooks=integrations.hooks("login_providers"))


async def _get_json(url: str, *, cache: bool = False, headers: dict | None = None) -> dict:
    if cache and url in _cache and _cache[url][0] > time.monotonic():
        return _cache[url][1]
    async with http_client() as client:
        r = await client.get(url, headers=headers or {})
    if r.status_code != 200:
        raise Refused("failed", f"GET {url.split('?')[0]} → {r.status_code}")
    data = r.json()
    if cache:
        _cache[url] = (time.monotonic() + CACHE_SECONDS, data)
    return data


def _entra_tenant(cfg: dict) -> str:
    return cfg.get("tenant_id") if cfg.get("tenant_mode", "single") == "single" else "organizations"


def discovery_url(provider: dict) -> str:
    if provider["kind"] == "entra":
        return f"{ENTRA_AUTHORITY}/{_entra_tenant(provider['config'])}/v2.0/.well-known/openid-configuration"
    return f"{GOOGLE_ISSUER}/.well-known/openid-configuration"


async def discovery(provider: dict) -> dict:
    return await _get_json(discovery_url(provider), cache=True)


async def authorize_url(provider: dict, *, state: str, nonce: str, verifier: str) -> str:
    cfg, kind = provider["config"], provider["kind"]
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    params = {"client_id": cfg["client_id"], "redirect_uri": urls(provider)["redirect_uri"], "response_type": "code",
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if KINDS[kind].protocol == "oidc":
        endpoint = (await discovery(provider))["authorization_endpoint"]
        params |= {"scope": SCOPES, "nonce": nonce, "response_mode": "query"}
        if kind == "entra":
            params["prompt"] = "select_account"
        if kind == "google":
            params["prompt"] = "select_account"
            if cfg.get("hosted_domain"):
                params["hd"] = cfg["hosted_domain"]
    else:
        endpoint = OAUTH[kind]["authorize"]
        params["scope"] = OAUTH[kind]["scope"]
        if kind == "github":
            params["allow_signup"] = "false"
    return f"{endpoint}?{urlencode(params)}"


async def _token(provider: dict, endpoint: str, code: str, verifier: str, secret: str) -> dict:
    cfg = provider["config"]
    async with http_client() as client:
        r = await client.post(endpoint, data={"grant_type": "authorization_code", "code": code,
                                              "redirect_uri": urls(provider)["redirect_uri"], "client_id": cfg["client_id"],
                                              "client_secret": secret, "code_verifier": verifier},
                              headers={"Accept": "application/json"})
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200 or "error" in data:
        raise Refused("failed", f"token → {r.status_code} {str(data.get('error', ''))[:60]}")
    return data


async def _verify_id_token(provider: dict, token: str, nonce: str) -> dict:
    meta = await discovery(provider)
    cfg, kind = provider["config"], provider["kind"]
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise Refused("failed", f"id_token header: {e}")
    if header.get("alg") != "RS256":
        raise Refused("failed", f"id_token alg {header.get('alg')}")
    jwks = await _get_json(meta["jwks_uri"], cache=True)
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if key is None:  # keys rotated: fetch once more
        _cache.pop(meta["jwks_uri"], None)
        jwks = await _get_json(meta["jwks_uri"], cache=True)
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if key is None:
        raise Refused("failed", "id_token kid unknown")
    try:
        claims = jwt.decode(token, jwt.PyJWK(key).key, algorithms=["RS256"], audience=cfg["client_id"], leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_iss": False})
    except jwt.PyJWTError as e:
        raise Refused("failed", f"id_token: {e}")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise Refused("failed", "nonce")
    iss = claims.get("iss", "")
    if kind == "google":
        ok = iss in (GOOGLE_ISSUER, "accounts.google.com")
    elif cfg.get("tenant_mode", "single") == "single":
        ok = iss == meta["issuer"]
    else:
        tid = str(claims.get("tid", "")).lower()
        ok = tid in cfg.get("tenants", []) and iss == meta["issuer"].replace("{tenantid}", tid)
    if not ok:
        raise Refused("failed", "issuer")
    return claims


def identity_from_claims(provider: dict, claims: dict) -> Identity:
    cfg, kind = provider["config"], provider["kind"]
    names = sorted(claims)
    if kind == "entra":
        email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower() or None
        # Entra does not verify e-mail on its own: it counts as verified only on a domain the admin listed (and so
        # vouches belongs to the tenant). The tenant itself is checked with the issuer.
        verified = bool(email) and domain_matches(email, cfg.get("domains") or [])
        groups_claim = claims.get("groups") or []
        return Identity(subject=f"{claims.get('tid')}:{claims.get('oid') or claims['sub']}", email=email,
                        email_verified=verified, name=claims.get("name"),
                        groups=[str(g) for g in groups_claim if isinstance(g, str)], claims=names, tenant=claims.get("tid"))
    email = (claims.get("email") or "").strip().lower() or None
    verified = bool(email) and claims.get("email_verified") in (True, "true")
    if cfg.get("hosted_domain") and claims.get("hd") != cfg["hosted_domain"]:
        raise Refused("domain", "hd")
    return Identity(subject=str(claims["sub"]), email=email, email_verified=verified, name=claims.get("name"), claims=names)


async def _oauth_identity(provider: dict, access_token: str) -> Identity:
    auth = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if provider["kind"] == "github":
        user = await _get_json("https://api.github.com/user", headers=auth)
        emails = await _get_json("https://api.github.com/user/emails", headers=auth)
        primary = next((e for e in emails if isinstance(e, dict) and e.get("primary")), None) if isinstance(emails, list) else None
        email = (primary or {}).get("email")
        return Identity(subject=str(user["id"]), email=email.lower() if email else None,
                        email_verified=bool(primary and primary.get("verified")), name=user.get("name") or user.get("login"),
                        claims=sorted(user))
    profile = await _get_json("https://api.amazon.com/user/profile", headers=auth)
    email = profile.get("email")
    # Login with Amazon does not say whether the e-mail is verified: it is only ever linked by the user from /konto.
    return Identity(subject=str(profile["user_id"]), email=email.lower() if email else None, email_verified=False,
                    name=profile.get("name"), claims=sorted(profile))


async def oidc_callback(conn, provider: dict, flow: dict, code: str) -> Identity:
    """Exchanges the code (with the PKCE verifier) and returns the verified identity."""
    secret = client_secret(provider["id"], (await db.fetch_one(
        conn, "select client_secret_enc from login_providers where id = %s", (provider["id"],)))["client_secret_enc"])
    kind = provider["kind"]
    if KINDS[kind].protocol == "oidc":
        meta = await discovery(provider)
        tokens = await _token(provider, meta["token_endpoint"], code, flow["verifier"], secret)
        if not tokens.get("id_token"):
            raise Refused("failed", "no id_token")
        return identity_from_claims(provider, await _verify_id_token(provider, tokens["id_token"], flow["nonce"]))
    tokens = await _token(provider, OAUTH[kind]["token"], code, flow["verifier"], secret)
    if not tokens.get("access_token"):
        raise Refused("failed", "no access_token")
    return await _oauth_identity(provider, tokens["access_token"])


# ── After the provider: which user ──────────────────────────────────────────

async def _create_user(conn, provider: dict, identity: Identity, meta: RequestMeta) -> dict:
    await conn.execute("select pg_advisory_xact_lock(7301948)")  # same lock as routes/auth.py when users are created
    existing = await db.fetch_one(conn, "select * from users where email = %s for update", (identity.email,))
    if existing:
        return existing
    audience = "internal" if access.internal_domain(identity.email) else "external"
    user = await db.fetch_one(conn, """insert into users (email, role, super_admin, audience, display_name)
                                       values (%s, 'user', false, %s, %s) returning *""",
                              (identity.email, audience, (identity.name or "")[:80] or None))
    await audit(conn, "create", actor_id=user["id"], meta=meta, table="users", row_id=user["id"],
                after={"email": identity.email, "role": "user", "audience": audience}, via=f"provider:{provider['key']}")
    await webhooks.emit(conn, "user.created", {"user_id": user["id"], "role": "user"})
    return user


async def resolve_user(conn, provider: dict, identity: Identity, meta: RequestMeta) -> dict:
    """The user this identity logs in as; links or creates as the rules allow. Raises Refused."""
    cfg = provider["config"]
    if cfg.get("domains") and not domain_matches(identity.email, cfg["domains"]):
        raise Refused("domain", domain_of(identity.email) or "no_email")
    linked = await db.fetch_one(conn, """select i.id as identity_id, u.* from user_identities i join users u on u.id = i.user_id
                                         where i.provider_id = %s and i.subject = %s for update of i""",
                                (provider["id"], identity.subject))
    if linked:
        user = linked
        await conn.execute("update user_identities set last_used_at = now(), email = %s where id = %s",
                           (identity.email, linked["identity_id"]))
    else:
        rules = await get_rules(conn)
        user = None
        if identity.email and identity.email_verified and rules["link"] == "verified_email":
            user = await db.fetch_one(conn, "select * from users where email = %s for update", (identity.email,))
            via = "verified_email"
        if user is None:
            may_create = (identity.email and identity.email_verified and cfg.get("new_users") == "signup"
                          and rules["new_users"] == "signup"
                          and signup.allows(await signup.get(conn), "email", identity.email))
            if not may_create:
                raise Refused("unverified" if identity.email and not identity.email_verified else "no_account",
                              "user_linked_only" if rules["link"] == "user_linked" else "")
            user = await _create_user(conn, provider, identity, meta)
            via = "signup"
        if user["impersonation_only"]:
            raise Refused("test_account")
        other = await db.fetch_one(conn, "select id from user_identities where provider_id = %s and user_id = %s",
                                   (provider["id"], user["id"]))
        if other:
            raise Refused("other_account")
        row = await db.fetch_one(conn, """insert into user_identities (user_id, provider_id, subject, email, linked_via, last_used_at)
                                          values (%s, %s, %s, %s, %s, now()) returning id""",
                                 (user["id"], provider["id"], identity.subject, identity.email, via))
        await audit(conn, "create", actor_id=user["id"], meta=meta, table="user_identities", row_id=row["id"],
                    after={"provider": provider["key"], "user_id": user["id"], "linked_via": via})
    if user["impersonation_only"]:
        raise Refused("test_account")
    if user["blocked_at"]:
        raise Refused("blocked")
    await conn.execute("update login_providers set last_login_at = now() where id = %s", (provider["id"],))
    await sync_groups(conn, provider, identity, user["id"], meta)
    return user


async def link_identity(conn, provider: dict, identity: Identity, user_id: int, meta: RequestMeta) -> None:
    """The user connected their account from /konto (they proved who they are first)."""
    cfg = provider["config"]
    if cfg.get("domains") and not domain_matches(identity.email, cfg["domains"]):
        raise Refused("domain", domain_of(identity.email) or "no_email")
    taken = await db.fetch_one(conn, "select user_id from user_identities where provider_id = %s and subject = %s",
                               (provider["id"], identity.subject))
    if taken and taken["user_id"] != user_id:
        raise Refused("taken")
    if taken:
        return
    if await db.fetch_one(conn, "select 1 from user_identities where provider_id = %s and user_id = %s",
                          (provider["id"], user_id)):
        raise Refused("other_account")
    row = await db.fetch_one(conn, """insert into user_identities (user_id, provider_id, subject, email, linked_via)
                                      values (%s, %s, %s, %s, 'user') returning id""",
                             (user_id, provider["id"], identity.subject, identity.email))
    await audit(conn, "create", actor_id=user_id, meta=meta, table="user_identities", row_id=row["id"],
                after={"provider": provider["key"], "user_id": user_id, "linked_via": "user"})
    await auth_event(conn, "provider.linked", ok=True, meta=meta, user_id=user_id, provider=provider["key"])


async def sync_groups(conn, provider: dict, identity: Identity, user_id: int, meta: RequestMeta) -> None:
    """The provider's groups → Gær groups (the mapping in its settings), at every login. Only memberships this provider
    added are removed again; admin roles never come from a provider."""
    mapping = provider["config"].get("group_map") or []
    if not mapping:
        return
    from app import secret_store
    have = set(identity.groups)
    for m in mapping:
        if m["claim"] in have:
            cur = await conn.execute("""insert into group_members (group_id, user_id, role, via_provider_id)
                                        select %s, %s, 'member', %s where exists (select 1 from groups where id = %s)
                                        on conflict (group_id, user_id) do nothing""",
                                     (m["group_id"], user_id, provider["id"], m["group_id"]))
            if cur.rowcount:
                await audit(conn, "group.member_added", actor_id=None, meta=meta, table="groups", row_id=m["group_id"],
                            user_id=user_id, role="member", via=f"provider:{provider['key']}")
                await secret_store.after_group_member_added(conn, m["group_id"], user_id, meta)
        else:
            cur = await conn.execute("""delete from group_members where group_id = %s and user_id = %s
                                        and via_provider_id = %s""", (m["group_id"], user_id, provider["id"]))
            if cur.rowcount:
                await audit(conn, "group.member_removed", actor_id=None, meta=meta, table="groups", row_id=m["group_id"],
                            user_id=user_id, via=f"provider:{provider['key']}")


async def record_test(conn, provider: dict, identity: Identity | None, actor_id: int, meta: RequestMeta,
                      error: str | None = None) -> dict:
    result = {"at": _now().isoformat(), "ok": identity is not None and error is None, "claims": identity.claims if identity else [],
              "email_domain": domain_of(identity.email) if identity else None,
              "email_verified": identity.email_verified if identity else None,
              "groups": len(identity.groups) if identity else 0, "error": error}
    await conn.execute("update login_providers set last_test = %s where id = %s", (Jsonb(result), provider["id"]))
    await audit(conn, "login_provider.tested", actor_id=actor_id, meta=meta, table="login_providers",
                row_id=provider["id"], ok=result["ok"], error=error)
    return result


async def emit_login(provider: dict) -> None:
    await events.emit("auth.provider_login", ref=provider["key"])


async def linked_count(conn, provider_id: int) -> int:
    return (await db.fetch_one(conn, "select count(*) as n from user_identities where provider_id = %s", (provider_id,)))["n"]


def valid_key(key: str) -> bool:
    return bool(_KEY.match(key))
