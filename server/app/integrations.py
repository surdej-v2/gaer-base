"""API connections made in code (shown in /admin/data/explore → Forbindelser).

Every outside API the app itself calls is described here: what it is for, which code calls it, which settings hold
its keys, and its endpoints. The httpx clients that call them get `hooks(key)`, which records each call as the event
`api.call` (app.events, kept 7 days): ref = "<integration>:<METHOD> <endpoint pattern>", value = milliseconds,
data = {"status": <HTTP status, 0 when no answer came>}. Only the registered pattern is recorded, never the real path or query, so no
account names, addresses or ids reach the events. A call that matches no pattern is recorded as "(andet)".

API sources made in /admin/data/connect are recorded the same way by app/data_sources.py as
"source:<kilde>:GET <sti>". Nothing here can be changed from /admin; the registry is code.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from app import config, events

log = logging.getLogger(__name__)

KIND = "api.call"


@dataclass(frozen=True)
class Endpoint:
    method: str | None        # None: any method (the call's own is recorded)
    path: str                 # what is shown and recorded, e.g. "/sms/{tjeneste}/jobs"
    label: str
    called_from: str
    pattern: str              # regex searched in the request path

    def matches(self, method: str, path: str) -> bool:
        return (self.method is None or self.method == method) and re.search(self.pattern, path) is not None


@dataclass(frozen=True)
class Integration:
    key: str
    name: str
    purpose: str
    files: tuple[str, ...]
    base: Callable[[], str | None]
    secrets: tuple[str, ...]           # environment variables that hold its keys
    active: Callable[[], bool]          # in use with the current settings
    endpoints: tuple[Endpoint, ...] = ()
    link: str | None = None             # the /admin page that manages it
    counted: bool = True                # False: the client is not httpx, calls are not recorded yet
    settings: tuple[str, ...] = field(default=())   # other settings it needs (not secret)


def _s():
    return config.settings


def _mail(provider: str) -> Callable[[], bool]:
    return lambda: _s().mail_provider == provider


REGISTRY: tuple[Integration, ...] = (
    Integration(
        "ai", "OVHcloud AI Endpoints", "AI-chat, agenter i Viden og embeddings til vidensrum.",
        ("server/app/ai.py", "server/app/knowledge/engine.py"), lambda: _s().ai_base_url, ("AI_API_KEY",),
        lambda: bool(_s().ai_api_key),
        (Endpoint("POST", "/chat/completions", "Chat, agenter og svar i Viden", "ai.py · knowledge/engine.py", r"/chat/completions$"),
         Endpoint("POST", "/embeddings", "Embeddings til vidensrum", "knowledge/engine.py", r"/embeddings$")),
        link="/ai", settings=("AI_BASE_URL", "AI_MODEL")),
    Integration(
        "lettermint", "Lettermint", "Udgående mail og status på leverede beskeder (svar kommer som webhook).",
        ("server/app/mail.py", "server/app/outbound_mail.py"), lambda: _s().lettermint_api_url,
        ("LETTERMINT_API_TOKEN", "LETTERMINT_TEAM_TOKEN"),
        lambda: _s().mail_provider == "lettermint" or bool(_s().lettermint_api_token),
        (Endpoint("POST", "/send", "Send mail", "mail.py · send_mail()", r"/send$"),
         Endpoint("GET", "/messages", "Status på sendte beskeder", "outbound_mail.py · job mail.lettermint_poll", r"/messages$")),
        link="/webhooks"),
    Integration(
        "ahasend", "AhaSend", "Udgående mail, når MAIL_PROVIDER=ahasend.", ("server/app/mail.py",),
        lambda: "https://api.ahasend.com", ("AHASEND_API_KEY",), _mail("ahasend"),
        (Endpoint("POST", "/v2/accounts/{konto}/messages", "Send mail", "mail.py · _ahasend()", r"^/v2/accounts/[^/]+/messages$"),),
        settings=("AHASEND_ACCOUNT_ID",)),
    Integration(
        "scaleway", "Scaleway Transactional Email", "Udgående mail, når MAIL_PROVIDER=scaleway.", ("server/app/mail.py",),
        lambda: "https://api.scaleway.com", ("SCALEWAY_SECRET_KEY",), _mail("scaleway"),
        (Endpoint("POST", "/transactional-email/v1alpha1/regions/{region}/emails", "Send mail", "mail.py · _scaleway()",
                  r"^/transactional-email/v1alpha1/regions/[^/]+/emails$"),),
        settings=("SCALEWAY_PROJECT_ID", "SCALEWAY_REGION")),
    Integration(
        "mailjet", "Mailjet", "Udgående mail, når MAIL_PROVIDER=mailjet.", ("server/app/mail.py",),
        lambda: "https://api.mailjet.com", ("MAILJET_API_KEY", "MAILJET_API_SECRET"), _mail("mailjet"),
        (Endpoint("POST", "/v3.1/send", "Send mail", "mail.py · _mailjet()", r"^/v3\.1/send$"),)),
    Integration(
        "brevo", "Brevo", "Udgående mail, når MAIL_PROVIDER=brevo.", ("server/app/mail.py",),
        lambda: "https://api.brevo.com", ("BREVO_API_KEY",), _mail("brevo"),
        (Endpoint("POST", "/v3/smtp/email", "Send mail", "mail.py · _brevo()", r"^/v3/smtp/email$"),)),
    Integration(
        "ovh_sms", "OVHcloud SMS", "Engangskoder og beskeder som SMS, når SMS_PROVIDER=ovh.", ("server/app/sms.py",),
        lambda: _s().ovh_endpoint, ("OVH_APPLICATION_KEY", "OVH_APPLICATION_SECRET", "OVH_CONSUMER_KEY"),
        lambda: _s().sms_provider == "ovh",
        (Endpoint("GET", "/auth/time", "Servertid til signaturen", "sms.py · _send_ovh()", r"/auth/time$"),
         Endpoint("POST", "/sms/{tjeneste}/jobs", "Send SMS", "sms.py · _send_ovh()", r"/sms/[^/]+/jobs$")),
        settings=("OVH_ENDPOINT", "OVH_SMS_SERVICE")),
    Integration(
        "stalwart", "Stalwart mailserver", "Postkasser, kalendere og kontakter (mailmodulet).", ("server/app/mailbox/backend.py",),
        lambda: _s().stalwart_url, ("STALWART_API_KEY", "STALWART_MASTER_SECRET"),
        lambda: _s().mailbox_backend == "stalwart",
        (Endpoint(None, "/api/principal…", "Konti, grupper og aliasser (admin-API)", "mailbox/backend.py", r"^/api/principal"),
         Endpoint(None, "/api/queue…", "Udgående kø", "mailbox/backend.py", r"^/api/queue"),
         Endpoint(None, "/api/settings…", "Indstillinger og genindlæsning", "mailbox/backend.py", r"^/api/(settings|reload)"),
         Endpoint(None, "/jmap…", "JMAP: mail, kalender og kontakter", "mailbox/backend.py", r"^/jmap"),
         Endpoint(None, "/dav…", "CalDAV og CardDAV", "mailbox/backend.py", r"^/dav"),
         Endpoint(None, "/.well-known, /healthz", "Opdagelse og sundhed", "mailbox/backend.py", r"^/(\.well-known|healthz)")),
        link="/mail", settings=("STALWART_URL", "STALWART_MASTER_USER")),
    Integration(
        "nats", "NATS-overvågning", "Læser NATS' overvågningsport til /admin/nats (kun læsning, intern).",
        ("server/app/nats_monitor.py",), lambda: _s().nats_monitor_url, (), lambda: bool(_s().nats_monitor_url),
        (Endpoint("GET", "/{x}z", "varz, jsz, connz …", "nats_monitor.py", r"^/[a-z]+z$"),), link="/nats",
        settings=("NATS_MONITOR_URL",)),
    Integration(
        "login_providers", "Login-udbydere", "Login med Microsoft Entra ID, Google, GitHub, Amazon og SAML (kun de udbydere, "
        "en admin har slået til).", ("server/app/login_providers.py", "server/app/saml.py"),
        lambda: "login.microsoftonline.com · accounts.google.com · github.com · amazon.com · jeres SAML-IdP", (),
        lambda: True,
        (Endpoint("GET", "/.well-known/openid-configuration", "OpenID Connect-opdagelse", "login_providers.py · discovery()",
                  r"/\.well-known/openid-configuration$"),
         Endpoint("GET", "/…/keys", "Signeringsnøgler (JWKS)", "login_providers.py · _verify_id_token()", r"/(keys|certs)$"),
         Endpoint("POST", "/…/token", "Kode → tokens", "login_providers.py · _token()", r"/(token|access_token)$"),
         Endpoint("GET", "/user, /user/emails, /user/profile", "Profil (GitHub, Amazon)", "login_providers.py · _oauth_identity()",
                  r"^/user(/emails|/profile)?$"),
         Endpoint("GET", "/…metadata…", "SAML-metadata", "saml.py · fetch_metadata()", r"(?i)metadata")),
        link="/login-providers"),
    Integration(
        "s3", "Object Storage (S3)", "Filer, avatarer og uploads. Kaldes med boto3, så kald tælles ikke endnu.",
        ("server/app/storage.py", "server/app/uploads.py"), lambda: _s().s3_endpoint, ("S3_ACCESS_KEY", "S3_SECRET_KEY"),
        lambda: _s().storage_backend == "s3", (), link="/files", counted=False, settings=("S3_ENDPOINT", "S3_BUCKET")),
)
BY_KEY = {i.key: i for i in REGISTRY}


def secret_is_set(name: str) -> bool:
    return bool(getattr(config.settings, name.lower(), None))


def ref_for(integration: Integration, method: str, path: str) -> str:
    for e in integration.endpoints:
        if e.matches(method, path):
            return f"{integration.key}:{method if e.method is None else e.method} {e.path}"
    return f"{integration.key}:{method} (andet)"


def hooks(key: str) -> dict:
    """event_hooks for an httpx.AsyncClient that calls the integration `key`."""
    integration = BY_KEY[key]

    async def on_request(request):
        request.extensions["gaer_t0"] = time.monotonic()

    async def on_response(response):
        try:
            request = response.request
            ms = (time.monotonic() - request.extensions.get("gaer_t0", time.monotonic())) * 1000
            await events.emit(KIND, ref=ref_for(integration, request.method, request.url.path)[:200], value=round(ms, 1),
                              data={"status": response.status_code})
        except Exception:  # noqa: BLE001 — counting must never break a call
            log.exception("api.call for %s not recorded", key)

    return {"request": [on_request], "response": [on_response]}


async def record_source_call(source_key: str, method: str, path: str, ms: float, status: int) -> None:
    """A call made for an API source from /admin/data/connect (path = the dataset's path from its description)."""
    try:
        await events.emit(KIND, ref=f"source:{source_key}:{method} {path}"[:200], value=round(ms, 1), data={"status": status})
    except Exception:  # noqa: BLE001
        log.exception("api.call for source %s not recorded", source_key)


STATS_SQL = """
    select ref, count(*) as calls,
           count(*) filter (where coalesce((data->>'status')::int, 0) not between 1 and 399) as errors,
           percentile_cont(0.95) within group (order by value) as p95_ms,
           max(ts) as last_at,
           max(ts) filter (where coalesce((data->>'status')::int, 0) not between 1 and 399) as last_error_at
    from events where kind = 'api.call' and ts > now() - make_interval(hours => %s) and ref like %s
    group by ref"""


async def stats(conn, prefix: str, hours: int = 24) -> dict[str, dict]:
    from app import db
    rows = await db.fetch_all(conn, STATS_SQL, (hours, prefix.replace("%", r"\%").replace("_", r"\_") + "%"))
    return {r["ref"]: r for r in rows}


def summarize(endpoint_rows: list[dict]) -> dict:
    calls = sum(r["calls"] for r in endpoint_rows)
    errors = sum(r["errors"] for r in endpoint_rows)
    p95 = max((r["p95_ms"] or 0 for r in endpoint_rows), default=None) if endpoint_rows else None
    last = max((r["last_at"] for r in endpoint_rows if r["last_at"]), default=None)
    last_error = max((r["last_error_at"] for r in endpoint_rows if r["last_error_at"]), default=None)
    return {"calls": calls, "errors": errors, "p95_ms": round(p95) if p95 is not None else None, "last_at": last,
            "last_error_at": last_error}
