"""Payments for the shop. PAYMENT_PROVIDER=console in development, mobilepay in production.

mobilepay uses the Vipps MobilePay ePayment API (https://developer.vippsmobilepay.com/docs/APIs/epayment-api/):
  1. create a payment with our reference → MobilePay gives a redirectUrl; the buyer approves in the app,
  2. MobilePay sends the buyer back to our returnUrl; we read the payment state,
  3. AUTHORIZED → we capture the full amount (the goods are taken right away) and the order is paid.

console never talks to anyone: the redirect goes to /w/butik/testbetaling/<reference>, where the
developer approves or rejects, and the state is kept on the order row. It is refused in production.
"""

import logging
import time
import uuid

import httpx

from app import config

log = logging.getLogger("gaer.payments")

# Provider states that end a payment without money moving.
FAILED_STATES = {"ABORTED", "EXPIRED", "TERMINATED"}


class PaymentError(RuntimeError):
    pass


def provider() -> str:
    name = config.settings.payment_provider
    if name not in ("console", "mobilepay"):
        raise PaymentError(f"unknown PAYMENT_PROVIDER {name!r}")
    if name == "console" and config.settings.is_production:
        raise PaymentError("PAYMENT_PROVIDER=console is not allowed in production")
    return name


def console_redirect(reference: str) -> str:
    return f"/w/butik/testbetaling/{reference}"


async def create_payment(*, reference: str, amount_ore: int, description: str, return_url: str) -> str:
    """Starts a payment and returns the URL the buyer must be sent to."""
    if provider() == "console":
        return console_redirect(reference)
    body = {
        "amount": {"currency": "DKK", "value": amount_ore},
        "paymentMethod": {"type": "WALLET"},
        "reference": reference,
        "returnUrl": return_url,
        "userFlow": "WEB_REDIRECT",
        "paymentDescription": description[:100],
    }
    data = await _request("POST", "/epayment/v1/payments", json=body, idempotency_key=reference)
    if not data.get("redirectUrl"):
        raise PaymentError("MobilePay returned no redirectUrl")
    return data["redirectUrl"]


async def get_state(reference: str) -> str:
    """CREATED, AUTHORIZED, ABORTED, EXPIRED or TERMINATED. Only for mobilepay; console keeps it on the order."""
    data = await _request("GET", f"/epayment/v1/payments/{reference}")
    return data.get("state") or "CREATED"


async def capture(reference: str, amount_ore: int) -> None:
    await _request("POST", f"/epayment/v1/payments/{reference}/capture",
                   json={"modificationAmount": {"currency": "DKK", "value": amount_ore}},
                   idempotency_key=f"capture-{reference}")


# ── Vipps MobilePay HTTP ────────────────────────────────────────────────────

_token: dict = {"value": None, "expires": 0.0}


def _check_configured() -> config.Settings:
    s = config.settings
    if not all((s.mobilepay_client_id, s.mobilepay_client_secret, s.mobilepay_subscription_key, s.mobilepay_msn)):
        raise PaymentError("MobilePay is not configured (MOBILEPAY_CLIENT_ID/SECRET, MOBILEPAY_SUBSCRIPTION_KEY, MOBILEPAY_MSN)")
    return s


def _base_headers(s: config.Settings) -> dict:
    return {
        "Ocp-Apim-Subscription-Key": s.mobilepay_subscription_key,
        "Merchant-Serial-Number": s.mobilepay_msn,
        "Vipps-System-Name": "gaer",
        "Vipps-System-Version": s.version,
    }


async def _access_token(client: httpx.AsyncClient, s: config.Settings) -> str:
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    r = await client.post(f"{s.mobilepay_api_url}/accesstoken/get", headers={
        **_base_headers(s), "client_id": s.mobilepay_client_id, "client_secret": s.mobilepay_client_secret})
    if r.status_code != 200:
        raise PaymentError(f"MobilePay access token failed: {r.status_code}")
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


async def _request(method: str, path: str, *, json: dict | None = None, idempotency_key: str | None = None) -> dict:
    s = _check_configured()
    async with httpx.AsyncClient(timeout=15) as client:
        headers = {**_base_headers(s), "Authorization": f"Bearer {await _access_token(client, s)}",
                   "Idempotency-Key": idempotency_key or str(uuid.uuid4())}
        r = await client.request(method, f"{s.mobilepay_api_url}{path}", json=json, headers=headers)
    if r.status_code >= 300:
        log.warning("MobilePay %s %s → %s %s", method, path, r.status_code, r.text[:500])
        raise PaymentError(f"MobilePay {method} {path} failed: {r.status_code}")
    return r.json() if r.content else {}
