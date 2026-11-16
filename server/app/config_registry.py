"""Which settings an admin may change in /admin/config, and which stay in the environment.

Every field of app.config.Settings is in exactly one place (tests/test_config_store.py checks it):

- KEYS: set in /admin/config. A plain key is stored as JSON in config_values; a secret key is encrypted in
  config_secrets and can be written, never read back. Each has a type and bounds that the server enforces.
- ENV_ONLY: read from the environment alone, with the reason. Root keys and the domain (the app needs them before it
  can read its own database or secrets), database URLs, storage, and switches that must never be flipped from a
  browser (DEMO_MODE, WEBHOOK_ALLOW_PRIVATE, YUBICO_ROOTS).

Login rules may only be tightened here: their bounds stop at today's defaults (rule 1 in the instructions). The
environment can still set any value, as before.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app import admin_roles


@dataclass(frozen=True)
class Group:
    label: str
    description: str
    roles: frozenset[str]   # admin roles that may see and change it (Fuld adgang always may)


@dataclass(frozen=True)
class Key:
    group: str
    label: str
    kind: str                       # str · text · int · float · bool · enum · url · email · host · domains
    help: str = ""
    options: tuple[str, ...] = ()   # enum
    min: float | None = None
    max: float | None = None
    max_len: int = 200
    pattern: str | None = None      # str: a full match
    allow_empty: bool = False       # str: "" is a value (e.g. no embeddings)
    secret: bool = False
    endpoint: str | None = None     # secret: the url key whose host it is bound to
    provider: str | None = None     # mail/SMS: shown only for this provider
    restart: bool = False           # read at start-up: takes effect after a restart
    not_in_production: tuple[str, ...] = ()   # enum values refused when GAER_ENV=production


DRIFT = frozenset({"drift"})
GROUPS: dict[str, Group] = {
    "login": Group("Login og sikkerhed", "Engangskoder, sessioner, anden faktor og robottjek. Kan kun strammes her; "
                   "grænserne stopper ved standardværdierne.", frozenset()),
    "mail": Group("E-mail", "Loginkoder, Beskeder og alle andre mails fra appen.", DRIFT),
    "sms": Group("SMS", "Loginkoder og Beskeder på SMS.", DRIFT),
    "mailserver": Group("Mail-modulet", "Postkasser, kalendere og kontakter (Stalwart). Forbindelsen til Stalwart "
                        "står i miljøet.", DRIFT),
    "files": Group("Filer og overførsler", "Upload, links og grænser for zip og eksport.", DRIFT),
    "ai": Group("AI og Viden", "AI-chat, agenter og embeddings til vidensrum.", frozenset({"produkt", "data"})),
    "mcp": Group("MCP", "MCP-serverne /mcp og /mcp/admin.", DRIFT),
    "license": Group("Licens", "Licensnøglen fra happymates.ai. Den er signeret, så den kan ikke forfalskes her.", DRIFT),
    "events": Group("Hændelser og NATS", "Hændelsesstrømmen og overvågningen af NATS.", DRIFT),
    "developer": Group("Developer", "Pipelines og cache i Gær Developer.", frozenset({"data"})),
}

_SENDER = r"[A-Za-z0-9 ]{1,11}"
_PREFIX = r"[a-z0-9][a-z0-9._/-]{0,100}/"

KEYS: dict[str, Key] = {
    # ── Login og sikkerhed (only tighter than the defaults) ──────────────────────────────────────────────────
    "rp_name": Key("login", "Navn ved passkeys", "str", "Navnet, telefonen og browseren viser, når man gemmer en passkey.",
                   max_len=80),
    "sms_login": Key("login", "Log ind med telefonnummer", "bool", "Brugere kan også logge ind med en kode på SMS."),
    "default_country_code": Key("login", "Landekode", "str", "Bruges, når et telefonnummer skrives uden landekode.",
                                pattern=r"\+[0-9]{1,4}"),
    "grace_logins": Key("login", "Grace logins for admins", "int", "Logins med kode, før /admin kræver anden faktor.",
                        min=0, max=3),
    "otp_ttl_seconds": Key("login", "Kodens levetid (sekunder)", "int", min=60, max=300),
    "otp_max_attempts": Key("login", "Forsøg pr. kode", "int", min=1, max=5),
    "otp_lock_minutes": Key("login", "Spærretid efter for mange forsøg (minutter)", "int", min=15, max=1440),
    "otp_max_per_phone_per_hour": Key("login", "Koder pr. telefon/e-mail i timen", "int", min=1, max=5),
    "otp_max_per_ip_per_hour": Key("login", "Koder pr. IP-adresse i timen", "int", min=1, max=30),
    "session_days": Key("login", "Sessionens levetid (dage)", "int", min=1, max=30),
    "mfa_session_minutes": Key("login", "Tid til anden faktor (minutter)", "int",
                               "Hvor længe et login venter på anden faktor.", min=1, max=15),
    "admin_mfa_hours": Key("login", "Anden faktor åbner /admin (timer)", "int", min=1, max=12),
    "stepup_minutes": Key("login", "Step-up gælder (minutter)", "int",
                          "Hvor længe en frisk anden faktor gælder for sikkerhedshandlinger.", min=1, max=15),
    "yubikey_link_hours": Key("login", "YubiKey-link gælder (timer)", "int", min=1, max=24),
    "captcha": Key("login", "Robottjek", "enum", "Proof of work foran login og oprettelse med kode.",
                   options=("pow", "off"), not_in_production=("off",)),
    "captcha_max_number": Key("login", "Robottjekkets sværhed", "int", "Browseren regner cirka halvt så mange "
                              "SHA-256-hashes.", min=50_000, max=5_000_000),
    "captcha_ttl_seconds": Key("login", "Robottjekket gælder (sekunder)", "int", min=60, max=600),

    # ── E-mail ──────────────────────────────────────────────────────────────────────────────────────────────
    "mail_provider": Key("mail", "Udbyder", "enum", options=("ahasend", "scaleway", "mailjet", "brevo", "lettermint",
                                                             "console"),
                         not_in_production=("console",)),
    "mail_from": Key("mail", "Afsenderadresse", "email", "Skal ligge på et domæne, der er verificeret hos udbyderen."),
    "mail_from_name": Key("mail", "Afsendernavn", "str", "Tomt: appens navn.", max_len=80, allow_empty=True),
    "ahasend_account_id": Key("mail", "AhaSend-konto-id", "str", provider="ahasend", max_len=100),
    "ahasend_api_key": Key("mail", "AhaSend API-nøgle", "str", secret=True, provider="ahasend"),
    "scaleway_project_id": Key("mail", "Scaleway-projekt-id", "str", provider="scaleway", max_len=100),
    "scaleway_region": Key("mail", "Scaleway-region", "str", provider="scaleway", pattern=r"[a-z]{2}-[a-z]{3}"),
    "scaleway_secret_key": Key("mail", "Scaleway secret key", "str", secret=True, provider="scaleway"),
    "mailjet_api_key": Key("mail", "Mailjet API-nøgle", "str", secret=True, provider="mailjet"),
    "mailjet_api_secret": Key("mail", "Mailjet API-hemmelighed", "str", secret=True, provider="mailjet"),
    "brevo_api_key": Key("mail", "Brevo API-nøgle", "str", secret=True, provider="brevo"),
    "lettermint_api_url": Key("mail", "Lettermint API-adresse", "url", "Skifter du vært, skal tokens tastes igen.",
                              provider="lettermint"),
    "lettermint_api_token": Key("mail", "Lettermint API-token", "str", "Et Project API-token.", secret=True,
                                endpoint="lettermint_api_url", provider="lettermint"),
    "lettermint_team_token": Key("mail", "Lettermint Team-token (valgfrit)", "str",
                                 "lm_team_…: lader /admin/mail hente status for sendt mail.", secret=True,
                                 endpoint="lettermint_api_url", provider="lettermint"),

    # ── SMS ─────────────────────────────────────────────────────────────────────────────────────────────────
    "sms_provider": Key("sms", "Udbyder", "enum", options=("ovh", "console"), not_in_production=("console",)),
    "sms_sender": Key("sms", "Afsender", "str", "Højst 11 tegn, bogstaver og tal. /admin/settings kan give et andet "
                      "navn til appen.", pattern=_SENDER),
    "ovh_endpoint": Key("sms", "OVH API-adresse", "url", "Skifter du vært, skal nøglerne tastes igen.", provider="ovh"),
    "ovh_sms_service": Key("sms", "OVH SMS-tjeneste", "str", provider="ovh", pattern=r"sms-[a-z0-9-]{3,60}"),
    "ovh_application_key": Key("sms", "OVH application key", "str", secret=True, endpoint="ovh_endpoint", provider="ovh"),
    "ovh_application_secret": Key("sms", "OVH application secret", "str", secret=True, endpoint="ovh_endpoint",
                                  provider="ovh"),
    "ovh_consumer_key": Key("sms", "OVH consumer key", "str", secret=True, endpoint="ovh_endpoint", provider="ovh"),

    # ── Mail-modulet ────────────────────────────────────────────────────────────────────────────────────────
    "mail_hostname": Key("mailserver", "Mailserverens værtsnavn", "host",
                         "Klienterne forbinder hertil (IMAP, SMTP, JMAP), og det er MX-målet."),
    "mail_default_quota_mb": Key("mailserver", "Standardkvote pr. postkasse (MB)", "int", min=100, max=1_048_576),
    "mail_spf_include": Key("mailserver", "SPF-include", "host", "Vises for nye domæner."),
    "lettermint_smtp_host": Key("mailserver", "Lettermint SMTP-vært", "host"),
    "lettermint_monthly_quota": Key("mailserver", "Lettermint-kvote pr. måned", "int", "0: vises ikke.",
                                    min=0, max=100_000_000),
    "mail_archive_prefix": Key("mailserver", "Præfiks for arkiv", "str", "Mappe i Object Storage, slutter med /.",
                               pattern=_PREFIX),
    "mail_backup_prefix": Key("mailserver", "Præfiks for backup", "str", "Mappe i Object Storage, slutter med /.",
                              pattern=_PREFIX),
    "stalwart_timeout": Key("mailserver", "Timeout mod Stalwart (sekunder)", "float", min=1, max=120),

    # ── Filer og overførsler ─────────────────────────────────────────────────────────────────────────────────
    "max_upload_mb": Key("files", "Største upload gennem serveren (MB)", "int", "Cloudflare tillader højst 100 MB.",
                         min=1, max=100),
    "direct_upload": Key("files", "Direkte upload til Object Storage", "bool",
                         "Browseren sender filen direkte til bucketen, forbi Cloudflares grænse."),
    "max_direct_upload_mb": Key("files", "Største direkte upload (MB)", "int", min=1, max=51_200),
    "upload_link_seconds": Key("files", "Uploadlinks gælder (sekunder)", "int", min=60, max=86_400),
    "unzip_max_files": Key("files", "Højst filer i en udpakket zip", "int", min=1, max=100_000),
    "unzip_max_mb": Key("files", "Højst MB i en udpakket zip", "int", min=1, max=102_400),
    "download_link_seconds": Key("files", "Downloadlinks gælder (sekunder)", "int", min=30, max=86_400),
    "transfer_max_mb": Key("files", "Største eksport- og importpakke (MB)", "int", min=1, max=51_200),

    # ── AI og Viden ─────────────────────────────────────────────────────────────────────────────────────────
    "ai_base_url": Key("ai", "AI-adresse", "url", "OpenAI-kompatibelt endpoint. Skifter du vært, skal nøglen tastes "
                       "igen."),
    "ai_api_key": Key("ai", "AI API-nøgle", "str", "Uden nøgle er AI slået fra overalt.", secret=True,
                      endpoint="ai_base_url"),
    "ai_model": Key("ai", "Model", "str", max_len=120),
    "ai_max_output_tokens": Key("ai", "Højst tokens i et svar", "int", min=100, max=32_000),
    "ai_max_context_chars": Key("ai", "Højst tegn kontekst", "int", min=1_000, max=500_000),
    "knowledge_embed_model": Key("ai", "Embeddingmodel", "str", "Tomt: kun fritekstsøgning i vidensrum.",
                                 max_len=120, allow_empty=True),
    "knowledge_max_zip_files": Key("ai", "Højst filer i en zip til Viden", "int", min=1, max=100_000),
    "knowledge_max_zip_mb": Key("ai", "Højst MB i en zip til Viden", "int", min=1, max=10_240),

    # ── MCP ─────────────────────────────────────────────────────────────────────────────────────────────────
    "mcp_app_enabled": Key("mcp", "/mcp (appen) slået til", "bool"),
    "mcp_admin_enabled": Key("mcp", "/mcp/admin slået til", "bool"),
    "mcp_draft_ttl_min": Key("mcp", "Kladder gælder (minutter)", "int", min=1, max=10),
    "mcp_access_token_minutes": Key("mcp", "Adgangstokens gælder (minutter)", "int", min=5, max=60),
    "mcp_refresh_token_days": Key("mcp", "Fornyelsestokens gælder (dage)", "int", min=1, max=30),
    "mcp_access_link_hours": Key("mcp", "Links i adgangsanmodninger gælder (timer)", "int", min=1, max=72),
    "internal_email_domains": Key("mcp", "Interne e-maildomæner", "domains",
                                  "Nye brugere med en adresse på et af dem starter som interne. Adskil med komma.",
                                  max_len=2000, allow_empty=True),

    # ── Licens ──────────────────────────────────────────────────────────────────────────────────────────────
    "license_key": Key("license", "Licensnøgle", "text", "En JWT fra happymates.ai. Signaturen og domænerne tjekkes.",
                       max_len=8000, allow_empty=True),

    # ── Hændelser og NATS ───────────────────────────────────────────────────────────────────────────────────
    "nats_monitor_timeout": Key("events", "Timeout mod NATS-overvågning (sekunder)", "float", min=1, max=60),
    "events_stream_max_mb": Key("events", "Hændelsesstrømmens største størrelse (MB)", "int", min=16, max=102_400,
                                restart=True),
    "events_rules_seconds": Key("events", "Regler tjekkes hvert (sekunder)", "float", min=1, max=60),

    # ── Developer ───────────────────────────────────────────────────────────────────────────────────────────
    "pipeline_preview_rows": Key("developer", "Rækker i forhåndsvisning", "int", min=1, max=1_000),
    "pipeline_preview_timeout_ms": Key("developer", "Timeout for forhåndsvisning (ms)", "int", min=100, max=60_000),
    "dev_cache_max_entries": Key("developer", "Højst poster i cachen", "int", min=100, max=1_000_000),
}

# Environment variables that stay there: field → (category, reason). Categories: "manual" (set by hand on the server),
# "compose" (set by deploy/compose.yml or the Gær Fleet console), "image" (the image's default is right),
# "optional" (only when the feature is used), "web" (the default for something chosen elsewhere in /admin).
MANUAL, COMPOSE, IMAGE, OPTIONAL, WEB = "manual", "compose", "image", "optional", "web"
ENV_ONLY: dict[str, tuple[str, str]] = {
    "secret_key": (MANUAL, "Rod-nøgle til HMAC af koder og kontaktoplysninger i logs."),
    "secret_box_keys": (MANUAL, "Krypterer hemmelighederne i databasen, også dem på denne side."),
    "totp_keys": (MANUAL, "Krypterer godkenderapps."),
    "origin": (MANUAL, "Appens adresse. Cookies, CORS og passkeys afhænger af den."),
    "rp_id": (MANUAL, "Passkey-domænet. Skiftes det, holder alle passkeys op med at virke."),
    "bootstrap_admin_email": (MANUAL, "Hvem der må blive den første admin, før der findes en."),
    "s3_endpoint": (MANUAL, "Object Storage."),
    "s3_region": (MANUAL, "Object Storage."),
    "s3_bucket": (MANUAL, "Object Storage. Skiftes bucketen, forsvinder alle filer for appen."),
    "s3_access_key": (MANUAL, "Object Storage."),
    "s3_secret_key": (MANUAL, "Object Storage."),
    "database_url": (COMPOSE, "Appens databasebruger."),
    "purge_database_url": (COMPOSE, "Brugeren, der må slette logdata."),
    "migration_database_url": (COMPOSE, "Ejer skemaet og kører migreringer."),
    "mcp_db_query_url": (COMPOSE, "Den skrivebeskyttede bruger til db_query."),
    "environment": (COMPOSE, "production eller development."),
    "version": (COMPOSE, "Sættes af buildet."),
    "trust_proxy": (COMPOSE, "Om klientens IP kommer fra X-Forwarded-For. Forkert værdi gør rate limits snydbare."),
    "nats_url": (COMPOSE, "NATS i samme stak."),
    "nats_monitor_url": (COMPOSE, "NATS' overvågningsport i samme stak."),
    "redirect_hosts": (COMPOSE, "Domænealiaser, sat af Gær Fleet."),
    "static_dir": (IMAGE, "Stien til det byggede frontend i imaget."),
    "extension_dir": (IMAGE, "Stien til udvidelsens kildekode i imaget."),
    "run_maintenance": (IMAGE, "Om denne container kører jobs (fra i tests)."),
    "job_workers": (IMAGE, "Antal job-workers pr. container, læses ved start."),
    "job_poll_seconds": (IMAGE, "Hvor tit workers kigger efter jobs."),
    "job_timeout_seconds": (IMAGE, "Længste tid for ét job."),
    "ingest_status_table": (IMAGE, "Tabelnavn, der sættes ind i SQL. Må aldrig komme fra en browser."),
    "demo_mode": (IMAGE, "Skriver loginkoder i serverloggen. Må aldrig kunne slås til fra en browser."),
    "webhook_allow_private": (IMAGE, "Tillader webhooks til private adresser (kun udvikling)."),
    "storage_backend": (OPTIONAL, "s3 (standard) eller azure."),
    "s3_public_endpoint": (OPTIONAL, "Kun når browseren skal bruge en anden adresse end S3_ENDPOINT."),
    "azure_storage_connection_string": (OPTIONAL, "Azure Blob Storage i stedet for S3."),
    "azure_storage_account": (OPTIONAL, "Azure Blob Storage i stedet for S3."),
    "azure_storage_key": (OPTIONAL, "Azure Blob Storage i stedet for S3."),
    "azure_storage_container": (OPTIONAL, "Azure Blob Storage i stedet for S3."),
    "mailbox_backend": (OPTIONAL, "Slår mail-modulet til (stalwart)."),
    "stalwart_url": (OPTIONAL, "Stalwart på det private netværk."),
    "stalwart_api_key": (OPTIONAL, "Administrerer alle postkasser."),
    "stalwart_master_user": (OPTIONAL, "Kan læse alle postkasser."),
    "stalwart_master_secret": (OPTIONAL, "Kan læse alle postkasser."),
    "redis_url": (OPTIONAL, "Cache og rate limits; uden den bruges Postgres."),
    "analytics_database_url": (OPTIONAL, "Gær Developer: snapshots i en anden database."),
    "pipeline_database_url": (OPTIONAL, "Gær Developer: rollen, pipelines skriver med."),
    "dev_fdw_admin_url": (OPTIONAL, "Gær Developer: rollen, der opretter fremmede servere."),
    "bootstrap_admin_phone": (OPTIONAL, "Som BOOTSTRAP_ADMIN_EMAIL, med SMS-login."),
    "yubico_roots": (OPTIONAL, "Tillidsankre for YubiKeys. Kan de skiftes, kan falske nøgler godkendes."),
    "web_front": (WEB, "Standardforsiden på /w/, indtil en admin vælger i /admin/web."),
}

ENV_NAMES = {"environment": "GAER_ENV", "version": "GAER_VERSION"}


def env_name(field_name: str) -> str:
    return ENV_NAMES.get(field_name, field_name.upper())


def may_use(group: str, roles) -> bool:
    roles = set(admin_roles.clean(roles))
    return admin_roles.FULL in roles or bool(GROUPS[group].roles & roles)


def host(url: str | None) -> str | None:
    if not url:
        return None
    return (urlparse(url).hostname or "").lower() or None


class Invalid(ValueError):
    pass


def coerce(key: str, value, *, production: bool = False):
    """The value as the field's type, or Invalid with a Danish message. Used on save and again on every load."""
    k = KEYS[key]
    if k.kind == "bool":
        if not isinstance(value, bool):
            raise Invalid(f"{k.label}: skal være ja eller nej")
        return value
    if k.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Invalid(f"{k.label}: skal være et tal")
        if k.kind == "int":
            if value != int(value):
                raise Invalid(f"{k.label}: skal være et helt tal")
            value = int(value)
        else:
            value = float(value)
        if (k.min is not None and value < k.min) or (k.max is not None and value > k.max):
            raise Invalid(f"{k.label}: skal være mellem {_n(k.min)} og {_n(k.max)}")
        return value
    if not isinstance(value, str):
        raise Invalid(f"{k.label}: skal være tekst")
    value = value.strip()
    if len(value) > k.max_len:
        raise Invalid(f"{k.label}: højst {k.max_len} tegn")
    if not value and k.kind != "domains":
        if k.allow_empty:
            return ""
        raise Invalid(f"{k.label}: må ikke være tom")
    if k.kind == "enum":
        if value not in k.options:
            raise Invalid(f"{k.label}: skal være en af {', '.join(k.options)}")
        if production and value in k.not_in_production:
            raise Invalid(f"{k.label}: {value} kan ikke vælges i produktion")
    elif k.kind == "url":
        parts = urlparse(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise Invalid(f"{k.label}: skal være en https-adresse uden brugernavn")
        value = value.rstrip("/")
    elif k.kind == "email":
        if not re.fullmatch(r"[^@\s]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
            raise Invalid(f"{k.label}: ikke en gyldig e-mailadresse")
    elif k.kind == "host":
        if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value.lower()):
            raise Invalid(f"{k.label}: ikke et gyldigt værtsnavn")
        value = value.lower()
    elif k.kind == "domains":
        parts = [d.strip().lower().lstrip("@") for d in value.split(",") if d.strip()]
        for d in parts:
            if not re.fullmatch(r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", d):
                raise Invalid(f"{k.label}: {d} er ikke et domæne")
        return tuple(dict.fromkeys(parts))
    elif k.kind == "str" and k.pattern and not re.fullmatch(k.pattern, value):
        raise Invalid(f"{k.label}: har ikke det rigtige format")
    elif k.kind == "str" and not k.secret and any(c in value for c in "\r\n\0"):
        raise Invalid(f"{k.label}: må ikke indeholde linjeskift")
    if k.secret and any(c in value for c in "\r\n\0"):
        raise Invalid(f"{k.label}: må ikke indeholde linjeskift")
    return value


def to_json(key: str, value):
    """How a coerced value is stored (tuples as lists)."""
    return list(value) if isinstance(value, tuple) else value


def _n(v) -> str:
    return f"{v:,.0f}".replace(",", ".") if v is not None and float(v).is_integer() else str(v)


def secret_keys() -> list[str]:
    return [k for k, v in KEYS.items() if v.secret]


def keys_in(group: str) -> list[str]:
    return [k for k, v in KEYS.items() if v.group == group]
