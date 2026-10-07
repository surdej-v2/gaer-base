"""Small security helpers: phone numbers, codes, tokens and request metadata."""

import hashlib
import hmac
import re
import secrets

from fastapi import Request

from app import config


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Returns E.164 (+4512345678). Numbers without a country code get DEFAULT_COUNTRY_CODE."""
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = config.settings.default_country_code + digits
    if not re.fullmatch(r"\+[1-9]\d{6,14}", digits):
        raise InvalidPhone("Ugyldigt telefonnummer")
    return digits


def bootstrap_admin_phone() -> str | None:
    """BOOTSTRAP_ADMIN_PHONE normalized to E.164, or None. Raises InvalidPhone if it is set but invalid."""
    raw = config.settings.bootstrap_admin_phone
    return normalize_phone(raw) if raw else None


def mask_phone(phone: str | None) -> str | None:
    """+4512345678 -> +45 •••• ••78. Used in logs and admin lists."""
    if not phone:
        return None
    cc_len = 3 if phone.startswith("+45") or phone.startswith("+46") or phone.startswith("+47") else min(4, len(phone) - 4)
    return f"{phone[:cc_len]} •••• ••{phone[-2:]}"


def _hmac(value: str, purpose: str) -> str:
    key = f"{config.settings.secret_key}:{purpose}".encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def phone_hash(phone: str | None) -> str | None:
    return _hmac(phone, "phone") if phone else None


def new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_hash(phone: str, code: str) -> str:
    return _hmac(f"{phone}:{code}", "otp")


def otp_matches(phone: str, code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(otp_hash(phone, code), stored_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str | None:
    """In production the container sits behind Caddy (TRUST_PROXY=1). Caddy appends the real
    peer address to X-Forwarded-For, so the last entry is the one we can trust."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and config.settings.trust_proxy:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else None


def user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:400]


def classify_device(ua: str) -> tuple[str, str]:
    """Returns (device, browser) from a user agent: ('mobile', 'Safari')."""
    u = ua.lower()
    if "ipad" in u or ("android" in u and "mobile" not in u) or "tablet" in u:
        device = "tablet"
    elif "mobi" in u or "iphone" in u or "android" in u:
        device = "mobile"
    elif not u or "bot" in u or "spider" in u or "crawl" in u:
        device = "bot" if u else "ukendt"
    else:
        device = "desktop"
    if "edg/" in u:
        browser = "Edge"
    elif "firefox" in u or "fxios" in u:
        browser = "Firefox"
    elif "chrome" in u or "crios" in u:
        browser = "Chrome"
    elif "safari" in u:
        browser = "Safari"
    else:
        browser = "Andet"
    return device, browser


def short_device(ua: str) -> str:
    device, browser = classify_device(ua)
    os_name = next((name for key, name in (("iphone", "iOS"), ("ipad", "iPadOS"), ("android", "Android"),
                                            ("mac os", "macOS"), ("windows", "Windows"), ("linux", "Linux"))
                    if key in ua.lower()), "")
    return " · ".join(p for p in (browser, os_name, device) if p)
