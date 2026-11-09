"""All configuration comes from environment variables. See .env.example."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # Database. DATABASE_URL is the app user: no UPDATE/DELETE on log tables.
    # PURGE_DATABASE_URL is the separate user that may delete log data (used from /admin only).
    # MIGRATION_DATABASE_URL owns the schema and runs migrations.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://postgres@localhost:5432/gaer"))
    purge_database_url: str | None = field(default_factory=lambda: _env("PURGE_DATABASE_URL"))
    migration_database_url: str | None = field(default_factory=lambda: _env("MIGRATION_DATABASE_URL"))

    # Secret used for HMAC of one-time codes and e-mail addresses/phone numbers in logs. Must be long and random in prod.
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-secret-change-me"))
    environment: str = field(default_factory=lambda: _env("GAER_ENV", "development"))
    version: str = field(default_factory=lambda: _env("GAER_VERSION", "dev"))

    # WebAuthn / passkeys
    rp_id: str = field(default_factory=lambda: _env("RP_ID", "localhost"))
    rp_name: str = field(default_factory=lambda: _env("RP_NAME", "Tørgær"))
    origin: str = field(default_factory=lambda: _env("ORIGIN", "http://localhost:5173"))

    # Optional: the only e-mail address (or, with SMS login, phone number) that may become the first admin.
    # Without them, whoever logs in first on a new instance becomes super admin. Set one in production.
    bootstrap_admin_email: str | None = field(default_factory=lambda: _env("BOOTSTRAP_ADMIN_EMAIL"))
    bootstrap_admin_phone: str | None = field(default_factory=lambda: _env("BOOTSTRAP_ADMIN_PHONE"))

    # Login: one-time codes are sent by e-mail. SMS_LOGIN=1 also lets users log in with a phone number.
    sms_login: bool = field(default_factory=lambda: _env("SMS_LOGIN", "0") == "1")
    # Demo: codes are written to the server log instead of being sent (also in production), never to the browser.
    # For trying an instance before mail is set up. Only whoever can read the server log can log in.
    demo_mode: bool = field(default_factory=lambda: _env("DEMO_MODE", "0") == "1")

    # Login policy. Admins without a second factor get GRACE_LOGINS logins with a code before /admin needs MFA.
    default_country_code: str = field(default_factory=lambda: _env("DEFAULT_COUNTRY_CODE", "+45"))
    grace_logins: int = field(default_factory=lambda: _int("GRACE_LOGINS", 3))
    otp_ttl_seconds: int = field(default_factory=lambda: _int("OTP_TTL_SECONDS", 300))
    otp_max_attempts: int = field(default_factory=lambda: _int("OTP_MAX_ATTEMPTS", 5))
    otp_lock_minutes: int = field(default_factory=lambda: _int("OTP_LOCK_MINUTES", 15))
    otp_max_per_phone_per_hour: int = field(default_factory=lambda: _int("OTP_MAX_PER_PHONE_PER_HOUR", 5))
    otp_max_per_ip_per_hour: int = field(default_factory=lambda: _int("OTP_MAX_PER_IP_PER_HOUR", 30))
    session_days: int = field(default_factory=lambda: _int("SESSION_DAYS", 30))
    # A pending login that still needs its second factor.
    mfa_session_minutes: int = field(default_factory=lambda: _int("MFA_SESSION_MINUTES", 15))
    # /admin: a second factor (or a grace login) counts for this long; security actions need one within STEPUP_MINUTES.
    admin_mfa_hours: int = field(default_factory=lambda: _int("ADMIN_MFA_HOURS", 12))
    stepup_minutes: int = field(default_factory=lambda: _int("STEPUP_MINUTES", 15))
    # Captcha on POST /api/auth/otp/start (account creation and login with a code): "pow" (proof of work, no third
    # party) or "off". CAPTCHA_MAX_NUMBER sets the work: the browser needs about half as many SHA-256 hashes.
    captcha: str = field(default_factory=lambda: _env("CAPTCHA", "pow"))
    captcha_max_number: int = field(default_factory=lambda: _int("CAPTCHA_MAX_NUMBER", 50_000))
    captcha_ttl_seconds: int = field(default_factory=lambda: _int("CAPTCHA_TTL_SECONDS", 600))
    # Authenticator-app secrets are encrypted with these keys ("<version>:<base64 32 bytes>,…", first = current).
    totp_keys: str | None = field(default_factory=lambda: _env("TOTP_KEYS"))

    # SMS: "console" (prints the code, dev only) or "ovh"
    sms_provider: str = field(default_factory=lambda: _env("SMS_PROVIDER", "console"))
    sms_sender: str = field(default_factory=lambda: _env("SMS_SENDER", "Gaer"))
    ovh_endpoint: str = field(default_factory=lambda: _env("OVH_ENDPOINT", "https://eu.api.ovh.com/1.0"))
    ovh_application_key: str | None = field(default_factory=lambda: _env("OVH_APPLICATION_KEY"))
    ovh_application_secret: str | None = field(default_factory=lambda: _env("OVH_APPLICATION_SECRET"))
    ovh_consumer_key: str | None = field(default_factory=lambda: _env("OVH_CONSUMER_KEY"))
    ovh_sms_service: str | None = field(default_factory=lambda: _env("OVH_SMS_SERVICE"))

    # E-mail: "ahasend" (default), "scaleway", "mailjet", "brevo", "lettermint" or "console" (prints the mail, dev only)
    mail_provider: str = field(default_factory=lambda: _env("MAIL_PROVIDER", "ahasend"))
    mail_from: str | None = field(default_factory=lambda: _env("MAIL_FROM"))
    mail_from_name: str | None = field(default_factory=lambda: _env("MAIL_FROM_NAME"))  # falls back to the app name
    ahasend_api_key: str | None = field(default_factory=lambda: _env("AHASEND_API_KEY"))
    ahasend_account_id: str | None = field(default_factory=lambda: _env("AHASEND_ACCOUNT_ID"))
    scaleway_secret_key: str | None = field(default_factory=lambda: _env("SCALEWAY_SECRET_KEY"))
    scaleway_project_id: str | None = field(default_factory=lambda: _env("SCALEWAY_PROJECT_ID"))
    scaleway_region: str = field(default_factory=lambda: _env("SCALEWAY_REGION", "fr-par"))
    mailjet_api_key: str | None = field(default_factory=lambda: _env("MAILJET_API_KEY"))
    mailjet_api_secret: str | None = field(default_factory=lambda: _env("MAILJET_API_SECRET"))
    brevo_api_key: str | None = field(default_factory=lambda: _env("BREVO_API_KEY"))
    lettermint_api_token: str | None = field(default_factory=lambda: _env("LETTERMINT_API_TOKEN"))

    # Object Storage: "s3" (OVHcloud Object Storage, MinIO locally) or "memory" (tests)
    storage_backend: str = field(default_factory=lambda: _env("STORAGE_BACKEND", "s3"))
    s3_endpoint: str | None = field(default_factory=lambda: _env("S3_ENDPOINT"))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION", "gra"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "gaer"))
    s3_access_key: str | None = field(default_factory=lambda: _env("S3_ACCESS_KEY"))
    s3_secret_key: str | None = field(default_factory=lambda: _env("S3_SECRET_KEY"))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 25))
    download_link_seconds: int = field(default_factory=lambda: _int("DOWNLOAD_LINK_SECONDS", 300))

    # Set to 1 when running behind the proxy in deploy/ (cloudflared), so client IPs come from X-Forwarded-For.
    trust_proxy: bool = field(default_factory=lambda: _env("TRUST_PROXY", "0") == "1")

    # Static frontend (built Vite output)
    # /w/ until an admin chooses in /admin/web: "markdown" (the page with slug '') or "facit246" (the 246 story).
    web_front: str = field(default_factory=lambda: _env("WEB_FRONT", "markdown"))
    static_dir: str = field(default_factory=lambda: _env("STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist")))

    # Optional AI chat. OpenAI-compatible endpoint; default OVHcloud AI Endpoints (processed in Europe).
    # The chat is hidden everywhere unless AI_API_KEY is set.
    ai_base_url: str = field(default_factory=lambda: _env("AI_BASE_URL", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1"))
    ai_api_key: str | None = field(default_factory=lambda: _env("AI_API_KEY"))
    ai_model: str = field(default_factory=lambda: _env("AI_MODEL", "Meta-Llama-3_3-70B-Instruct"))
    ai_max_output_tokens: int = field(default_factory=lambda: _int("AI_MAX_OUTPUT_TOKENS", 1500))
    ai_max_context_chars: int = field(default_factory=lambda: _int("AI_MAX_CONTEXT_CHARS", 24000))

    # Viden (specs/knowledge): embeddings for the vidensrum. Empty KNOWLEDGE_EMBED_MODEL (or no AI_API_KEY) = full-text
    # search only. Answers are written by AI_MODEL; without AI the agent returns the best excerpts.
    knowledge_embed_model: str = field(default_factory=lambda: _env("KNOWLEDGE_EMBED_MODEL", "bge-m3"))
    knowledge_max_zip_files: int = field(default_factory=lambda: _int("KNOWLEDGE_MAX_ZIP_FILES", 1000))
    knowledge_max_zip_mb: int = field(default_factory=lambda: _int("KNOWLEDGE_MAX_ZIP_MB", 200))

    # MCP servers (specs/mcp): /mcp for the app, /mcp/admin for admin. 0 turns a server off (404).
    mcp_app_enabled: bool = field(default_factory=lambda: _env("MCP_APP_ENABLED", "1") == "1")
    mcp_admin_enabled: bool = field(default_factory=lambda: _env("MCP_ADMIN_ENABLED", "1") == "1")
    mcp_draft_ttl_min: int = field(default_factory=lambda: _int("MCP_DRAFT_TTL_MIN", 10))
    mcp_access_token_minutes: int = field(default_factory=lambda: _int("MCP_ACCESS_TOKEN_MINUTES", 60))
    mcp_refresh_token_days: int = field(default_factory=lambda: _int("MCP_REFRESH_TOKEN_DAYS", 30))
    # Access requests from MCP clients (app/access.py): e-mail links live this long; new users with an e-mail on one
    # of these domains (comma separated, e.g. "firma.dk,firma.com") start as internal instead of external.
    mcp_access_link_hours: int = field(default_factory=lambda: _int("MCP_ACCESS_LINK_HOURS", 72))
    internal_email_domains: tuple[str, ...] = field(default_factory=lambda: tuple(
        d.strip().lower().lstrip("@") for d in (_env("INTERNAL_EMAIL_DOMAINS", "") or "").split(",") if d.strip()))
    # The read-only <db>_mcp_ro user for db_query. Without it the tool answers "ikke tilgængelig".
    mcp_db_query_url: str | None = field(default_factory=lambda: _env("MCP_DB_QUERY_URL"))

    # Browser extension source, zipped by /api/extension/download
    extension_dir: str = field(default_factory=lambda: _env("EXTENSION_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "extension")))

    # Background jobs: the worker and the scheduler (retention, cleanup, webhook deliveries). Disabled in tests.
    run_maintenance: bool = field(default_factory=lambda: _env("RUN_MAINTENANCE", "1") == "1")
    job_workers: int = field(default_factory=lambda: _int("JOB_WORKERS", 2))
    job_poll_seconds: float = field(default_factory=lambda: float(_env("JOB_POLL_SECONDS", "2")))
    job_timeout_seconds: int = field(default_factory=lambda: _int("JOB_TIMEOUT_SECONDS", 600))

    # Outgoing webhooks. Secrets and auth headers are encrypted with SECRET_BOX_KEYS (see app.secrets_box).
    # WEBHOOK_ALLOW_PRIVATE=1 allows http and private/loopback addresses (development only).
    secret_box_keys: str | None = field(default_factory=lambda: _env("SECRET_BOX_KEYS"))
    webhook_allow_private: bool = field(default_factory=lambda: _env("WEBHOOK_ALLOW_PRIVATE", "0") == "1")

    # NATS (the "nats" service in compose). NATS_URL is for clients in the app; NATS_MONITOR_URL is the server's
    # HTTP monitoring port, read by /admin/nats. Without NATS_MONITOR_URL the page says NATS is not set up.
    nats_url: str | None = field(default_factory=lambda: _env("NATS_URL"))
    nats_monitor_url: str | None = field(default_factory=lambda: _env("NATS_MONITOR_URL"))
    nats_monitor_timeout: float = field(default_factory=lambda: float(_env("NATS_MONITOR_TIMEOUT", "5")))

    # Mail, calendar and contacts (specs/mail): Stalwart holds the mailboxes, Lettermint relays all outbound mail.
    # MAILBOX_BACKEND: "off" (the module answers "not set up"), "stalwart", or "memory" (development and tests).
    mailbox_backend: str = field(default_factory=lambda: _env("MAILBOX_BACKEND", "off"))
    # Stalwart's HTTP listener on the private network: management API (/api/…), JMAP and WebDAV. Never public.
    stalwart_url: str | None = field(default_factory=lambda: _env("STALWART_URL"))
    # "<admin principal>:<secret>" (HTTP Basic; deploy/mail/bootstrap.sh creates the principal "gaer-api") or a bearer token
    stalwart_api_key: str | None = field(default_factory=lambda: _env("STALWART_API_KEY"))
    # Master user for acting as an account over JMAP/WebDAV (retention, calendars, contacts): "<account>%<user>".
    stalwart_master_user: str | None = field(default_factory=lambda: _env("STALWART_MASTER_USER"))
    stalwart_master_secret: str | None = field(default_factory=lambda: _env("STALWART_MASTER_SECRET"))
    stalwart_timeout: float = field(default_factory=lambda: float(_env("STALWART_TIMEOUT", "15")))
    # The public host clients connect to (IMAP 993, submission 465/587, JMAP/CalDAV/CardDAV 443) and the MX target.
    mail_hostname: str | None = field(default_factory=lambda: _env("MAIL_HOSTNAME"))
    mail_default_quota_mb: int = field(default_factory=lambda: _int("MAIL_DEFAULT_QUOTA_MB", 5120))
    # SPF include shown for new domains (check the value on the domain's page in Lettermint) and the relay's host.
    mail_spf_include: str = field(default_factory=lambda: _env("MAIL_SPF_INCLUDE", "spf.lettermint.co"))
    lettermint_smtp_host: str = field(default_factory=lambda: _env("LETTERMINT_SMTP_HOST", "smtp.lettermint.co"))
    lettermint_monthly_quota: int = field(default_factory=lambda: _int("LETTERMINT_MONTHLY_QUOTA", 0))  # 0: not shown
    # Object Storage prefixes (same bucket as the app unless the deployment points them elsewhere): mail archived by
    # the retention job when archive export is on, and the nightly calendar/contacts backup.
    mail_archive_prefix: str = field(default_factory=lambda: _env("MAIL_ARCHIVE_PREFIX", "mail-archive/"))
    mail_backup_prefix: str = field(default_factory=lambda: _env("MAIL_BACKUP_PREFIX", "mail-backup/"))

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.origin.startswith("https://")


settings = Settings()


def data_secret(name: str) -> str | None:
    """Keys for API sources in /admin → Data. A kildebeskrivelse names the variable; only DATA_SECRET_* can be read,
    so a description can never send another setting (DATABASE_URL, SECRET_KEY …) to an API."""
    if not name.startswith("DATA_SECRET_") or not name.replace("_", "").isalnum() or not name.isupper():
        return None
    return _env(name)


def reload() -> Settings:
    global settings
    settings = Settings()
    return settings
