"""Login: phone number + one-time code, three grace logins, then passkey.

  1. POST /otp/start      sends a code (stored only as a hash, short-lived, single use)
  2. POST /otp/verify     first user on a new instance becomes admin.
                          Without a passkey and with grace logins left -> full session, grace.used.
                          Otherwise -> enroll session that can only register a passkey
                          (grace exhausted, or a new device / lost passkey; does not use a grace login).
  3. POST /passkey/register/*  registers a passkey; an enroll session is upgraded to full.
  4. POST /passkey/login/*     normal login from then on.

The rules are described in the gaer-auth skill and must not be bypassed.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from webauthn import (base64url_to_bytes, generate_authentication_options, generate_registration_options,
                      options_to_json, verify_authentication_response, verify_registration_response)
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

from app import config, db, settings_store
from app.logs import RequestMeta, audit, auth_event
from app.security import (InvalidPhone, bootstrap_admin_phone, new_otp_code, normalize_phone, otp_hash,
                          otp_matches, phone_hash)
from app.sessions import (Principal, any_session, clear_session_cookie, create_session, optional_principal,
                          request_meta, revoke_sessions)
from app.sms import SmsError, otp_message, send_sms

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PhoneIn(BaseModel):
    phone: str = Field(max_length=32)


class VerifyIn(PhoneIn):
    code: str = Field(min_length=4, max_length=8)


class RegisterIn(BaseModel):
    challenge_id: str
    credential: dict
    name: str | None = Field(default=None, max_length=60)


class LoginIn(BaseModel):
    challenge_id: str
    credential: dict


def _phone(raw: str) -> str:
    try:
        return normalize_phone(raw)
    except InvalidPhone as e:
        raise HTTPException(400, str(e))


async def _count_events(conn, event: str, *, since_minutes: int, phone: str | None = None,
                        ip: str | None = None, result: str | None = None) -> int:
    where = ["event = %s", "ts > now() - make_interval(mins => %s)"]
    params: list = [event, since_minutes]
    if phone:
        where.append("phone_hash = %s")
        params.append(phone_hash(phone))
    if ip:
        where.append("ip = %s")
        params.append(ip)
    if result:
        where.append("result = %s")
        params.append(result)
    row = await db.fetch_one(conn, f"select count(*) as n from auth_log where {' and '.join(where)}", params)
    return row["n"]


async def _is_locked(conn, phone: str) -> bool:
    s = config.settings
    failed = await _count_events(conn, "otp.failed", since_minutes=s.otp_lock_minutes, phone=phone)
    return failed >= s.otp_max_attempts


# ── One-time codes ──────────────────────────────────────────────────────────

async def check_send_limits(conn, phone: str, meta: RequestMeta, **detail) -> None:
    """Lock after too many wrong codes, and rate limits per number and per IP. Raises 429 (and logs it)."""
    s = config.settings
    if await _is_locked(conn, phone):
        await auth_event(conn, "otp.sent", ok=False, meta=meta, phone=phone, reason="locked", **detail)
        await conn.commit()
        raise HTTPException(429, "For mange forkerte forsøg. Prøv igen om lidt.")
    per_phone = await _count_events(conn, "otp.sent", since_minutes=60, phone=phone, result="ok")
    per_ip = await _count_events(conn, "otp.sent", since_minutes=60, ip=meta.ip, result="ok") if meta.ip else 0
    if per_phone >= s.otp_max_per_phone_per_hour or per_ip >= s.otp_max_per_ip_per_hour:
        await auth_event(conn, "otp.sent", ok=False, meta=meta, phone=phone, reason="rate_limited", **detail)
        await conn.commit()
        raise HTTPException(429, "Der er sendt for mange koder. Prøv igen senere.")


async def issue_code(conn, phone: str, meta: RequestMeta, *, purpose: str = "login", user_id: int | None = None) -> str:
    """Stores a new one-time code (hashed) for the number and purpose, and sends it by SMS."""
    s = config.settings
    code = new_otp_code()
    await conn.execute("""update otp_codes set consumed_at = now()
                          where phone = %s and purpose = %s and consumed_at is null""", (phone, purpose))
    await conn.execute("""insert into otp_codes (phone, purpose, user_id, code_hash, expires_at)
                          values (%s, %s, %s, %s, now() + make_interval(secs => %s))""",
                       (phone, purpose, user_id, otp_hash(phone, code), s.otp_ttl_seconds))
    values = await settings_store.get_all(conn)
    detail = {} if purpose == "login" else {"purpose": purpose}
    try:
        await send_sms(phone, otp_message(code, values["app_name"], max(1, s.otp_ttl_seconds // 60)),
                       values["sms_sender"])
    except SmsError as e:
        await conn.rollback()
        await auth_event(conn, "otp.sent", ok=False, meta=meta, phone=phone, user_id=user_id, reason="sms_error",
                         error=str(e), **detail)
        await conn.commit()
        raise HTTPException(502, "Vi kunne ikke sende SMS'en. Prøv igen.")
    await auth_event(conn, "otp.sent", ok=True, meta=meta, phone=phone, user_id=user_id, **detail)
    return code


async def consume_code(conn, phone: str, code: str, meta: RequestMeta, *, purpose: str = "login",
                       user_id: int | None = None) -> None:
    """Checks a one-time code and marks it used. Wrong codes count towards the lock. Raises 400/429."""
    s = config.settings
    detail = {} if purpose == "login" else {"purpose": purpose}
    if await _is_locked(conn, phone):
        await auth_event(conn, "otp.failed", ok=False, meta=meta, phone=phone, reason="locked", **detail)
        await conn.commit()
        raise HTTPException(429, "For mange forkerte forsøg. Prøv igen om lidt.")
    otp = await db.fetch_one(conn, """
        select id, code_hash, attempts from otp_codes
        where phone = %s and purpose = %s and consumed_at is null and expires_at > now()
          and (%s::bigint is null or user_id = %s)
        order by created_at desc limit 1 for update""", (phone, purpose, user_id, user_id))
    if otp is None or not otp_matches(phone, code.strip(), otp["code_hash"]):
        if otp is not None:
            attempts = otp["attempts"] + 1
            await conn.execute(
                "update otp_codes set attempts = %s, consumed_at = case when %s then now() end where id = %s",
                (attempts, attempts >= s.otp_max_attempts, otp["id"]))
        await auth_event(conn, "otp.failed", ok=False, meta=meta, phone=phone, user_id=user_id,
                         reason="no_active_code" if otp is None else "wrong_code", **detail)
        await conn.commit()
        raise HTTPException(400, "Koden er forkert eller udløbet")
    await conn.execute("update otp_codes set consumed_at = now() where id = %s", (otp["id"],))


@router.post("/otp/start")
async def otp_start(body: PhoneIn, request: Request):
    s = config.settings
    meta = request_meta(request)
    phone = _phone(body.phone)
    async with db.connection() as conn:
        await check_send_limits(conn, phone, meta)
        user = await db.fetch_one(conn, "select id, blocked_at from users where phone = %s", (phone,))
        if user and user["blocked_at"]:
            # Answer as usual so the endpoint does not reveal which numbers are blocked.
            await auth_event(conn, "otp.sent", ok=False, meta=meta, phone=phone, user_id=user["id"],
                             reason="blocked")
            return {"ok": True, "expires_in": s.otp_ttl_seconds}
        code = await issue_code(conn, phone, meta, user_id=user["id"] if user else None)

    response = {"ok": True, "expires_in": s.otp_ttl_seconds}
    if s.sms_provider == "console" and not s.is_production:
        response["dev_code"] = code
    return response


@router.post("/otp/verify")
async def otp_verify(body: VerifyIn, request: Request, response: Response):
    s = config.settings
    meta = request_meta(request)
    phone = _phone(body.phone)
    async with db.connection() as conn:
        await consume_code(conn, phone, body.code, meta)
        user = await _get_or_create_user(conn, phone, meta)
        if user["blocked_at"]:
            await auth_event(conn, "otp.verified", ok=False, meta=meta, phone=phone, user_id=user["id"],
                             reason="blocked")
            await conn.commit()
            raise HTTPException(403, "Din bruger er spærret. Kontakt en administrator.")
        await auth_event(conn, "otp.verified", ok=True, meta=meta, phone=phone, user_id=user["id"])

        has_passkey = (await db.fetch_one(conn, "select exists(select 1 from passkeys where user_id = %s) as e",
                                          (user["id"],)))["e"]
        if not has_passkey and user["grace_logins_used"] < s.grace_logins:
            used = user["grace_logins_used"] + 1
            await conn.execute(
                "update users set grace_logins_used = %s, last_login_at = now(), updated_at = now() where id = %s",
                (used, user["id"]))
            await auth_event(conn, "grace.used", ok=True, meta=meta, phone=phone, user_id=user["id"],
                             used=used, of=s.grace_logins)
            await create_session(conn, response, user["id"], "full", meta)
            return {"status": "logged_in", "grace_left": s.grace_logins - used, "offer_passkey": True,
                    "became_admin": user.get("became_admin", False)}

        await create_session(conn, response, user["id"], "enroll", meta)
        return {"status": "passkey_required", "reason": "new_device" if has_passkey else "grace_exhausted"}


async def admin_open_to_anyone(conn) -> bool:
    """True when the next person to log in becomes admin: a new instance without BOOTSTRAP_ADMIN_PHONE."""
    if bootstrap_admin_phone():
        return False
    return (await db.fetch_one(conn, "select not exists (select 1 from users) as open"))["open"]


async def _get_or_create_user(conn, phone: str, meta: RequestMeta) -> dict:
    """Creates the user on first login and decides who becomes admin:
    - BOOTSTRAP_ADMIN_PHONE set: only that number becomes admin, and only while the instance has no admin.
      If it already exists as a normal user then, it is promoted.
    - not set: the very first user becomes admin."""
    bootstrap = bootstrap_admin_phone()
    user = await db.fetch_one(conn, "select * from users where phone = %s for update", (phone,))
    if user and (phone != bootstrap or user["role"] == "admin"):
        return user  # the normal case: an existing user, no admin decision to make
    # Creating a user or promoting the bootstrap number: serialize so only one becomes the first admin.
    await conn.execute("select pg_advisory_xact_lock(7301948)")
    user = await db.fetch_one(conn, "select * from users where phone = %s for update", (phone,))
    if user:
        if phone == bootstrap and user["role"] != "admin" and not await _has_admin(conn):
            await conn.execute("update users set role = 'admin', updated_at = now() where id = %s", (user["id"],))
            await audit(conn, "role.changed", actor_id=user["id"], meta=meta, table="users", row_id=user["id"],
                        before={"role": user["role"]}, after={"role": "admin"}, reason="bootstrap_admin_phone")
            user = {**user, "role": "admin", "became_admin": True}
        return user

    if bootstrap:
        role = "admin" if phone == bootstrap and not await _has_admin(conn) else "user"
    else:
        exists = (await db.fetch_one(conn, "select exists (select 1 from users) as e"))["e"]
        role = "user" if exists else "admin"
    user = await db.fetch_one(conn, "insert into users (phone, role) values (%s, %s) returning *", (phone, role))
    await audit(conn, "create", actor_id=user["id"], meta=meta, table="users", row_id=user["id"],
                after={"phone": phone, "role": role})
    if role == "admin":
        await audit(conn, "admin.first_user", actor_id=user["id"], meta=meta, table="users", row_id=user["id"],
                    via="bootstrap_admin_phone" if bootstrap else "first_login")
        user["became_admin"] = True
    return user


async def _has_admin(conn) -> bool:
    return (await db.fetch_one(conn, "select exists (select 1 from users where role = 'admin') as e"))["e"]


# ── Passkeys ────────────────────────────────────────────────────────────────

async def _store_challenge(conn, challenge: bytes, kind: str, user_id: int | None) -> str:
    challenge_id = secrets.token_urlsafe(24)
    await conn.execute(
        "insert into webauthn_challenges (id, challenge, kind, user_id, expires_at) values (%s, %s, %s, %s, %s)",
        (challenge_id, challenge, kind, user_id, datetime.now(timezone.utc) + timedelta(minutes=5)))
    return challenge_id


async def _take_challenge(conn, challenge_id: str, kind: str, user_id: int | None) -> bytes:
    row = await db.fetch_one(conn, """
        delete from webauthn_challenges where id = %s and kind = %s and expires_at > now()
        returning challenge, user_id""", (challenge_id, kind))
    if row is None or (user_id is not None and row["user_id"] != user_id):
        raise HTTPException(400, "Forespørgslen er udløbet. Prøv igen.")
    return bytes(row["challenge"])


@router.post("/passkey/register/options")
async def passkey_register_options(principal: Principal = Depends(any_session)):
    s = config.settings
    async with db.connection() as conn:
        existing = await db.fetch_all(conn, "select credential_id from passkeys where user_id = %s",
                                      (principal.user_id,))
        options = generate_registration_options(
            rp_id=s.rp_id,
            rp_name=s.rp_name,
            user_id=str(principal.user_id).encode(),
            user_name=principal.phone,
            user_display_name=principal.display_name or principal.phone,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(r["credential_id"])) for r in existing],
        )
        challenge_id = await _store_challenge(conn, options.challenge, "register", principal.user_id)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


@router.post("/passkey/register/verify")
async def passkey_register_verify(body: RegisterIn, response: Response, principal: Principal = Depends(any_session)):
    s = config.settings
    meta = principal.meta
    async with db.connection() as conn:
        challenge = await _take_challenge(conn, body.challenge_id, "register", principal.user_id)
        try:
            verified = verify_registration_response(credential=body.credential, expected_challenge=challenge,
                                                    expected_rp_id=s.rp_id, expected_origin=s.origin)
        except InvalidRegistrationResponse as e:
            await auth_event(conn, "passkey.registered", ok=False, meta=meta, user_id=principal.user_id,
                             phone=principal.phone, reason=str(e))
            await conn.commit()
            raise HTTPException(400, "Passkey kunne ikke registreres")
        transports = body.credential.get("response", {}).get("transports") or []
        name = (body.name or "").strip() or _default_passkey_name(meta.user_agent)
        row = await db.fetch_one(conn, """
            insert into passkeys (user_id, credential_id, public_key, sign_count, transports, name)
            values (%s, %s, %s, %s, %s, %s) returning id""",
            (principal.user_id, verified.credential_id, verified.credential_public_key, verified.sign_count,
             transports, name))
        await auth_event(conn, "passkey.registered", ok=True, meta=meta, user_id=principal.user_id,
                         phone=principal.phone, passkey_id=row["id"], via=principal.scope)
        await audit(conn, "create", actor_id=principal.user_id, meta=meta, table="passkeys", row_id=row["id"],
                    after={"name": name, "user_id": principal.user_id})
        if principal.scope == "enroll":
            await conn.execute("update sessions set revoked_at = now() where id = %s", (principal.session_id,))
            await conn.execute("update users set last_login_at = now() where id = %s", (principal.user_id,))
            await create_session(conn, response, principal.user_id, "full", meta)
    return {"ok": True, "passkey_id": row["id"]}


def _default_passkey_name(ua: str) -> str:
    from app.security import short_device
    return short_device(ua) or "Passkey"


@router.post("/passkey/login/options")
async def passkey_login_options():
    options = generate_authentication_options(rp_id=config.settings.rp_id,
                                              user_verification=UserVerificationRequirement.PREFERRED)
    async with db.connection() as conn:
        challenge_id = await _store_challenge(conn, options.challenge, "login", None)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


@router.post("/passkey/login/verify")
async def passkey_login_verify(body: LoginIn, request: Request, response: Response):
    s = config.settings
    meta = request_meta(request)
    async with db.connection() as conn:
        challenge = await _take_challenge(conn, body.challenge_id, "login", None)
        try:
            raw_id = base64url_to_bytes(body.credential.get("rawId") or body.credential.get("id", ""))
        except Exception:
            raw_id = b""
        passkey = await db.fetch_one(conn, """
            select p.*, u.phone, u.blocked_at from passkeys p join users u on u.id = p.user_id
            where p.credential_id = %s""", (raw_id,))
        if passkey is None:
            await auth_event(conn, "passkey.login", ok=False, meta=meta, reason="unknown_credential")
            await conn.commit()
            raise HTTPException(400, "Passkey blev ikke genkendt. Log ind med telefonnummer.")
        try:
            verified = verify_authentication_response(
                credential=body.credential, expected_challenge=challenge, expected_rp_id=s.rp_id,
                expected_origin=s.origin, credential_public_key=bytes(passkey["public_key"]),
                credential_current_sign_count=passkey["sign_count"])
        except InvalidAuthenticationResponse as e:
            await auth_event(conn, "passkey.login", ok=False, meta=meta, user_id=passkey["user_id"],
                             phone=passkey["phone"], reason=str(e))
            await conn.commit()
            raise HTTPException(400, "Passkey kunne ikke bekræftes")
        if passkey["blocked_at"]:
            await auth_event(conn, "passkey.login", ok=False, meta=meta, user_id=passkey["user_id"],
                             phone=passkey["phone"], reason="blocked")
            await conn.commit()
            raise HTTPException(403, "Din bruger er spærret. Kontakt en administrator.")
        await conn.execute("update passkeys set sign_count = %s, last_used_at = now() where id = %s",
                           (verified.new_sign_count, passkey["id"]))
        await conn.execute("update users set last_login_at = now() where id = %s", (passkey["user_id"],))
        await auth_event(conn, "passkey.login", ok=True, meta=meta, user_id=passkey["user_id"],
                         phone=passkey["phone"], passkey_id=passkey["id"])
        await create_session(conn, response, passkey["user_id"], "full", meta)
    return {"status": "logged_in"}


# ── Session ─────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(request: Request, response: Response, principal: Principal | None = Depends(optional_principal)):
    if principal:
        async with db.connection() as conn:
            await revoke_sessions(conn, meta=principal.meta, user_id=principal.user_id,
                                  session_id=principal.session_id, reason="logout")
    clear_session_cookie(response, request_meta(request).embedded)
    return {"ok": True}


@router.get("/me")
async def me(principal: Principal | None = Depends(optional_principal)):
    if principal is None:
        return {"user": None}
    async with db.connection() as conn:
        user = await db.fetch_one(conn, """
            select u.id, u.phone, u.display_name, u.role, u.grace_logins_used,
                   (select count(*) from passkeys p where p.user_id = u.id) as passkeys
            from users u where u.id = %s""", (principal.user_id,))
    return {
        "user": {
            "id": user["id"],
            "phone": user["phone"],
            "display_name": user["display_name"],
            "role": user["role"],
            "passkeys": user["passkeys"],
            "grace_left": max(0, config.settings.grace_logins - user["grace_logins_used"]) if not user["passkeys"] else 0,
        },
        "scope": principal.scope,
    }
