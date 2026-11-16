"""Session cookies and the FastAPI dependencies that guard endpoints.

Two scopes:
  full  normal login
  mfa   a login that still needs its second factor (users with "Kræv passkey"): may only call /api/auth/mfa/*

A full session also records when it proved a second factor (mfa_at) and whether it is an admin grace login (grace).
/admin needs one of them, see require_admin and app.mfa.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response

from app import admin_roles, config, db, mfa
from app.logs import RequestMeta, audit, auth_event
from app.mfa import mfa_error
from app.security import client_ip, new_token, token_hash, user_agent

COOKIE = "gaer_session"
IMP_COOKIE = "gaer_imp"  # the impersonated test account's session, used only by the app on /
IMPERSONATION_PATH = "/api/impersonation"
REAL_ONLY_PATHS = ("/api/admin", IMPERSONATION_PATH)


@dataclass
class Principal:
    session_id: int
    user_id: int
    email: str | None
    phone: str | None
    role: str
    scope: str
    display_name: str | None
    meta: RequestMeta
    impersonator_id: int | None = None  # set when an admin is viewing the app as this test account
    super_admin: bool = False
    admin_roles: tuple[str, ...] = ()    # app/admin_roles.py: which parts of /admin
    mfa_at: datetime | None = None       # when this session proved a second factor
    grace: bool = False                  # an admin grace login without a second factor
    yubikey_at: datetime | None = None   # when this session proved a YubiKey
    yubikey_required: bool = False       # users.yubikey_required: /admin and step-up count only a YubiKey
    created_at: datetime | None = None
    # The real admin while this browser impersonates a test account with the role user: /admin is closed until the
    # impersonation stops (only /api/impersonation answers), so the admin cannot see admin content "as" a user.
    impersonating_user: bool = False

    @property
    def contact(self) -> str | None:
        return self.email or self.phone

    @property
    def is_admin(self) -> bool:
        return (self.role == "admin" and self.scope == "full" and self.impersonator_id is None
                and not self.impersonating_user)


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip=client_ip(request), user_agent=user_agent(request),
                       embedded=request.headers.get("x-gaer-embedded") == "1")


def set_session_cookie(response: Response, token: str, max_age: int, embedded: bool) -> None:
    """Inside the browser extension or an Office pane the app is a third-party iframe. There the
    cookie must be SameSite=None and Partitioned (CHIPS), so it only exists inside that embedding.
    CSRF is still covered by the X-Gaer header requirement."""
    s = config.settings
    if embedded and s.secure_cookies:
        response.headers.append("set-cookie", _partitioned(token, max_age))
    else:
        response.set_cookie(COOKIE, token, max_age=max_age, httponly=True, secure=s.secure_cookies,
                            samesite="lax", path="/")


def _partitioned(value: str, max_age: int) -> str:
    # Written by hand: Starlette only supports Partitioned on Python 3.14+.
    return f"{COOKIE}={value}; Max-Age={max_age}; Path=/; HttpOnly; Secure; SameSite=None; Partitioned"


def clear_session_cookie(response: Response, embedded: bool) -> None:
    s = config.settings
    if embedded and s.secure_cookies:
        response.headers.append("set-cookie", _partitioned("", 0))
    else:
        response.delete_cookie(COOKIE, path="/")


async def create_session(conn, response: Response, user_id: int, scope: str, meta: RequestMeta, *,
                         mfa: bool = False, grace: bool = False, yubikey: bool = False) -> int:
    """Always a new random token: a pending or code-only session is never upgraded in place."""
    assert scope in ("full", "mfa")
    token = new_token()
    s = config.settings
    lifetime = timedelta(days=s.session_days) if scope == "full" else timedelta(minutes=s.mfa_session_minutes)
    expires = datetime.now(timezone.utc) + lifetime
    row = await db.fetch_one(conn,
        """insert into sessions (user_id, token_hash, scope, expires_at, ip, user_agent, mfa_at, grace, yubikey_at)
           values (%s, %s, %s, %s, %s, %s, case when %s then now() end, %s, case when %s then now() end) returning id""",
        (user_id, token_hash(token), scope, expires, meta.ip, meta.user_agent, mfa, grace, mfa and yubikey),
    )
    set_session_cookie(response, token, int(lifetime.total_seconds()), meta.embedded)
    return row["id"]


async def revoke_sessions(conn, *, meta: RequestMeta, user_id: int, session_id: int | None = None,
                          reason: str, actor_id: int | None = None) -> int:
    """Revokes one session or all of a user's sessions and writes session.revoked."""
    if session_id is not None:
        cur = await conn.execute(
            "update sessions set revoked_at = now() where id = %s and user_id = %s and revoked_at is null",
            (session_id, user_id))
    else:
        cur = await conn.execute(
            "update sessions set revoked_at = now() where user_id = %s and revoked_at is null", (user_id,))
    if cur.rowcount:
        await auth_event(conn, "session.revoked", ok=True, meta=meta, user_id=user_id, reason=reason,
                         count=cur.rowcount, by=actor_id)
    return cur.rowcount


def wants_real_session(request: Request) -> bool:
    """/admin, the impersonation endpoints and every call from the admin app always run as the real
    admin. Only the app on / runs as the impersonated test account."""
    path = request.url.path
    return (path.startswith(REAL_ONLY_PATHS) or request.headers.get("x-gaer-app") == "admin")


async def _session_row(conn, token: str, impersonator_id: int | None = None) -> dict | None:
    if impersonator_id is None:
        where = "s.impersonator_id is null and not u.impersonation_only"
        params: tuple = (token_hash(token),)
    else:
        where = "s.impersonator_id = %s and u.impersonation_only and u.impersonation_owner_id = %s"
        params = (token_hash(token), impersonator_id, impersonator_id)
    row = await db.fetch_one(conn, f"""
        select s.id as session_id, s.scope, s.last_seen_at, s.mfa_at, s.grace, s.created_at, s.yubikey_at,
               u.id as user_id, u.email, u.phone, u.role, u.yubikey_required,
               u.super_admin, u.admin_roles, u.display_name, u.blocked_at
        from sessions s join users u on u.id = s.user_id
        where s.token_hash = %s and s.revoked_at is null and s.expires_at > now() and {where}""", params)
    if row is None or row["blocked_at"] is not None:
        return None
    if datetime.now(timezone.utc) - row["last_seen_at"] > timedelta(minutes=5):
        await conn.execute("update sessions set last_seen_at = now() where id = %s", (row["session_id"],))
    return row


async def _load(request: Request) -> Principal | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    imp_token = request.cookies.get(IMP_COOKIE)
    async with db.connection() as conn:
        row = await _session_row(conn, token)
        if row is None:
            return None
        meta = request_meta(request)
        impersonating_user = False
        if imp_token and row["role"] == "admin" and row["scope"] == "full":
            imp = await _session_row(conn, imp_token, impersonator_id=row["user_id"])
            if imp is not None and not wants_real_session(request):
                meta.impersonator_id = row["user_id"]
                return Principal(session_id=imp["session_id"], user_id=imp["user_id"], email=imp["email"],
                                 phone=imp["phone"], role=imp["role"], scope=imp["scope"],
                                 display_name=imp["display_name"], meta=meta, impersonator_id=row["user_id"])
            impersonating_user = imp is not None and imp["role"] != "admin"
    return Principal(session_id=row["session_id"], user_id=row["user_id"], email=row["email"], phone=row["phone"],
                     role=row["role"], scope=row["scope"], display_name=row["display_name"], meta=meta,
                     super_admin=row["super_admin"], admin_roles=tuple(row["admin_roles"] or ()), mfa_at=row["mfa_at"], grace=row["grace"],
                     created_at=row["created_at"], yubikey_at=row["yubikey_at"], yubikey_required=row["yubikey_required"],
                     impersonating_user=impersonating_user)


async def optional_principal(request: Request) -> Principal | None:
    return await _load(request)


async def any_session(request: Request) -> Principal:
    """Full or pending ('mfa') session. Only the second-factor endpoints (/api/auth/mfa/*) accept pending sessions."""
    principal = await _load(request)
    if principal is None:
        raise HTTPException(401, "Ikke logget ind")
    return principal


async def current_user(principal: Principal = Depends(any_session)) -> Principal:
    if principal.scope != "full":
        raise mfa_error("mfa_required", "Bekræft med din passkey, sikkerhedsnøgle eller godkendelsesapp for at fortsætte")
    return principal


def admin_mfa_ok(principal: Principal) -> bool:
    """A second factor within ADMIN_MFA_HOURS, or a grace login younger than that. With the YubiKey requirement:
    a YubiKey within ADMIN_MFA_HOURS, and no grace logins."""
    window = timedelta(hours=config.settings.admin_mfa_hours)
    if principal.yubikey_required:
        return mfa.within(principal.yubikey_at, window)
    return mfa.within(principal.mfa_at, window) or (principal.grace and mfa.within(principal.created_at, window))


async def require_admin(request: Request, principal: Principal = Depends(current_user)) -> Principal:
    """Everything in /admin is audited. Changes write their own detailed entries;
    reads are logged here as admin.read with the path and query.
    After the grace logins /admin also needs a second factor (403 + X-Gaer-Error: mfa_required / mfa_setup_required).
    While this browser impersonates a test account with the role user, /admin is closed (403 + X-Gaer-Error: impersonating)."""
    if principal.impersonating_user:
        # Only the impersonation endpoints answer (status, stop, switch), so the admin can get back.
        if not request.url.path.startswith(IMPERSONATION_PATH + "/"):
            raise HTTPException(403, "Du efterligner en almindelig bruger. Stop efterligningen for at bruge /admin.",
                                headers={mfa.ERROR_HEADER: "impersonating"})
    elif not principal.is_admin:
        raise HTTPException(403, "Kræver admin-rollen")
    if not admin_roles.allows(principal.admin_roles, request.method, request.url.path):
        raise HTTPException(403, "Din admin-rolle giver ikke adgang til denne del af /admin")
    async with db.connection() as conn:
        if not admin_mfa_ok(principal):
            if principal.yubikey_required:
                if (await mfa.factors(conn, principal.user_id))["yubikeys"]:
                    raise mfa_error("yubikey_required", "Bekræft med din YubiKey")
                raise mfa_error("yubikey_setup_required",
                                "Din konto kræver en YubiKey til administrationen. Bed en administrator om at tilknytte en.")
            if (await mfa.factors(conn, principal.user_id))["total"]:
                raise mfa_error("mfa_required", "Bekræft med din passkey, sikkerhedsnøgle eller godkendelsesapp")
            raise mfa_error("mfa_setup_required",
                            "Administrationen kræver en passkey, sikkerhedsnøgle eller godkendelsesapp. Opret en for at fortsætte.")
        if request.method == "GET":
            await audit(conn, "admin.read", actor_id=principal.user_id, meta=principal.meta,
                        path=request.url.path, query=str(request.url.query) or None)
    return principal


async def require_admin_stepup(principal: Principal = Depends(require_admin)) -> Principal:
    """Security actions in /admin (roles, contact details, blocking, MFA reset, erasure, impersonation …): a second
    factor proven within STEPUP_MINUTES (a YubiKey for users with the YubiKey requirement). A grace login is not enough."""
    mfa.require_stepup(principal)
    return principal
