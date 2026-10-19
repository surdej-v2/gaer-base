"""Session cookies and the FastAPI dependencies that guard endpoints.

Two scopes:
  full    normal login
  enroll  after a one-time code when a passkey is required: may only register a passkey
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response

from app import config, db
from app.logs import RequestMeta, audit, auth_event
from app.security import client_ip, new_token, token_hash, user_agent

COOKIE = "gaer_session"
IMP_COOKIE = "gaer_imp"  # the impersonated test account's session, used only by the app on /
REAL_ONLY_PATHS = ("/api/admin", "/api/impersonation")


@dataclass
class Principal:
    session_id: int
    user_id: int
    phone: str
    role: str
    scope: str
    display_name: str | None
    meta: RequestMeta
    impersonator_id: int | None = None  # set when an admin is viewing the app as this test account

    @property
    def is_admin(self) -> bool:
        return self.role == "admin" and self.scope == "full" and self.impersonator_id is None


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


async def create_session(conn, response: Response, user_id: int, scope: str, meta: RequestMeta) -> None:
    token = new_token()
    s = config.settings
    lifetime = timedelta(days=s.session_days) if scope == "full" else timedelta(minutes=s.enroll_session_minutes)
    expires = datetime.now(timezone.utc) + lifetime
    await conn.execute(
        """insert into sessions (user_id, token_hash, scope, expires_at, ip, user_agent)
           values (%s, %s, %s, %s, %s, %s)""",
        (user_id, token_hash(token), scope, expires, meta.ip, meta.user_agent),
    )
    set_session_cookie(response, token, int(lifetime.total_seconds()), meta.embedded)


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
        select s.id as session_id, s.scope, s.last_seen_at, u.id as user_id, u.phone, u.role,
               u.display_name, u.blocked_at
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
        if imp_token and row["role"] == "admin" and row["scope"] == "full" and not wants_real_session(request):
            imp = await _session_row(conn, imp_token, impersonator_id=row["user_id"])
            if imp is not None:
                meta.impersonator_id = row["user_id"]
                return Principal(session_id=imp["session_id"], user_id=imp["user_id"], phone=imp["phone"], role=imp["role"],
                                 scope=imp["scope"], display_name=imp["display_name"], meta=meta,
                                 impersonator_id=row["user_id"])
    return Principal(session_id=row["session_id"], user_id=row["user_id"], phone=row["phone"],
                     role=row["role"], scope=row["scope"], display_name=row["display_name"], meta=meta)


async def optional_principal(request: Request) -> Principal | None:
    return await _load(request)


async def any_session(request: Request) -> Principal:
    """Full or enroll session. Only the passkey registration endpoints accept enroll sessions."""
    principal = await _load(request)
    if principal is None:
        raise HTTPException(401, "Ikke logget ind")
    return principal


async def current_user(principal: Principal = Depends(any_session)) -> Principal:
    if principal.scope != "full":
        raise HTTPException(403, "Opret en passkey for at fortsætte")
    return principal


async def require_admin(request: Request, principal: Principal = Depends(current_user)) -> Principal:
    """Everything in /admin is audited. Changes write their own detailed entries;
    reads are logged here as admin.read with the path and query."""
    if not principal.is_admin:
        raise HTTPException(403, "Kræver admin-rollen")
    if request.method == "GET":
        async with db.connection() as conn:
            await audit(conn, "admin.read", actor_id=principal.user_id, meta=principal.meta,
                        path=request.url.path, query=str(request.url.query) or None)
    return principal
