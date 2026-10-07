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

    # Secret used for HMAC of one-time codes and phone numbers in logs. Must be long and random in prod.
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-secret-change-me"))
    environment: str = field(default_factory=lambda: _env("GAER_ENV", "development"))
    version: str = field(default_factory=lambda: _env("GAER_VERSION", "dev"))

    # WebAuthn / passkeys
    rp_id: str = field(default_factory=lambda: _env("RP_ID", "localhost"))
    rp_name: str = field(default_factory=lambda: _env("RP_NAME", "Gær"))
    origin: str = field(default_factory=lambda: _env("ORIGIN", "http://localhost:5173"))

    # Optional: the only phone number that may become the first admin. Without it, whoever logs in first
    # on a new instance becomes admin. Set it in production so nobody else can claim a fresh instance.
    bootstrap_admin_phone: str | None = field(default_factory=lambda: _env("BOOTSTRAP_ADMIN_PHONE"))

    # Login policy
    default_country_code: str = field(default_factory=lambda: _env("DEFAULT_COUNTRY_CODE", "+45"))
    grace_logins: int = field(default_factory=lambda: _int("GRACE_LOGINS", 3))
    otp_ttl_seconds: int = field(default_factory=lambda: _int("OTP_TTL_SECONDS", 300))
    otp_max_attempts: int = field(default_factory=lambda: _int("OTP_MAX_ATTEMPTS", 5))
    otp_lock_minutes: int = field(default_factory=lambda: _int("OTP_LOCK_MINUTES", 15))
    otp_max_per_phone_per_hour: int = field(default_factory=lambda: _int("OTP_MAX_PER_PHONE_PER_HOUR", 5))
    otp_max_per_ip_per_hour: int = field(default_factory=lambda: _int("OTP_MAX_PER_IP_PER_HOUR", 30))
    session_days: int = field(default_factory=lambda: _int("SESSION_DAYS", 30))
    enroll_session_minutes: int = field(default_factory=lambda: _int("ENROLL_SESSION_MINUTES", 15))

    # SMS: "console" (prints the code, dev only) or "ovh"
    sms_provider: str = field(default_factory=lambda: _env("SMS_PROVIDER", "console"))
    sms_sender: str = field(default_factory=lambda: _env("SMS_SENDER", "Gaer"))
    ovh_endpoint: str = field(default_factory=lambda: _env("OVH_ENDPOINT", "https://eu.api.ovh.com/1.0"))
    ovh_application_key: str | None = field(default_factory=lambda: _env("OVH_APPLICATION_KEY"))
    ovh_application_secret: str | None = field(default_factory=lambda: _env("OVH_APPLICATION_SECRET"))
    ovh_consumer_key: str | None = field(default_factory=lambda: _env("OVH_CONSUMER_KEY"))
    ovh_sms_service: str | None = field(default_factory=lambda: _env("OVH_SMS_SERVICE"))

    # Object Storage: "s3" (OVHcloud Object Storage, MinIO locally) or "memory" (tests)
    storage_backend: str = field(default_factory=lambda: _env("STORAGE_BACKEND", "s3"))
    s3_endpoint: str | None = field(default_factory=lambda: _env("S3_ENDPOINT"))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION", "gra"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "gaer"))
    s3_access_key: str | None = field(default_factory=lambda: _env("S3_ACCESS_KEY"))
    s3_secret_key: str | None = field(default_factory=lambda: _env("S3_SECRET_KEY"))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 25))
    download_link_seconds: int = field(default_factory=lambda: _int("DOWNLOAD_LINK_SECONDS", 300))

    # Set to 1 when running behind the Caddy proxy from deploy/, so client IPs come from X-Forwarded-For.
    trust_proxy: bool = field(default_factory=lambda: _env("TRUST_PROXY", "0") == "1")

    # Static frontend (built Vite output)
    static_dir: str = field(default_factory=lambda: _env("STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist")))

    # Optional AI chat. OpenAI-compatible endpoint; default OVHcloud AI Endpoints (processed in Europe).
    # The chat is hidden everywhere unless AI_API_KEY is set.
    ai_base_url: str = field(default_factory=lambda: _env("AI_BASE_URL", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1"))
    ai_api_key: str | None = field(default_factory=lambda: _env("AI_API_KEY"))
    ai_model: str = field(default_factory=lambda: _env("AI_MODEL", "Meta-Llama-3_3-70B-Instruct"))
    ai_max_output_tokens: int = field(default_factory=lambda: _int("AI_MAX_OUTPUT_TOKENS", 1500))
    ai_max_context_chars: int = field(default_factory=lambda: _int("AI_MAX_CONTEXT_CHARS", 24000))

    # Browser extension source, zipped by /api/extension/download
    extension_dir: str = field(default_factory=lambda: _env("EXTENSION_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "extension")))

    # Background maintenance (retention, cleanup). Disabled in tests.
    run_maintenance: bool = field(default_factory=lambda: _env("RUN_MAINTENANCE", "1") == "1")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.origin.startswith("https://")


settings = Settings()


def reload() -> Settings:
    global settings
    settings = Settings()
    return settings
