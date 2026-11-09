"""Instance settings edited in /admin/settings, /admin/consent and /admin/retention."""

from psycopg.types.json import Jsonb

from app import config
from app.db import fetch_all

DEFAULTS: dict = {
    "app_name": "Tørgær",
    "logo_key": None,
    "sms_sender": None,             # falls back to SMS_SENDER
    "gdpr_email": "",
    "consent_version": 1,
    "consent_title": "Må vi tælle med?",
    "consent_text": "Vi bruger ikke cookies fra tredjepart. Siger du ja, gemmer vi et tilfældigt id i din browser, "
                    "så vi kan se, hvordan siden bliver brugt, og hvor der bliver klikket. Data bliver hos os i Europa.",
    "consent_accept": "Ja tak",
    "consent_decline": "Kun det nødvendige",
    "retention_auth_log_days": 365,
    "retention_audit_log_days": 1825,
    "retention_analytics_days": 395,
    "retention_mcp_log_days": 90,
    "retention_last_run": None,
    "ai_default_monthly_tokens": 200_000,   # per user; 0 turns AI off for users without their own limit
    "ai_default_warn_pct": 80,              # warn users without a budget at this share of their limit; 0: never
    "ai_warning_mail": True,                # e-mail users when they pass a warning level, reach the cap or use up a budget
    "og_images": {},                        # og:image per anonymous surface → storage key (app/og.py)
    "web_front": "markdown",                # /w/: "markdown" (slug '') or "facit246" (app/web_front.py); default: WEB_FRONT
    "mail_archive_export": False,           # mail: copy .eml to the archive before expiry; users may request a download
    "mail_relay_rotated_at": None,          # mail: when the Lettermint SMTP credentials were last rotated
    "mail_relay_paused": False,             # mail: outbound queue paused from /admin/mail → Drift
    "mail_last_sync": None,                 # mail: {at, mailboxes, drift} from the nightly sync
    "mail_last_backup": None,               # mail: {at, calendars, address_books, failures} from the nightly backup
}

PUBLIC_KEYS = ("app_name", "logo_key", "consent_version", "consent_title", "consent_text",
               "consent_accept", "consent_decline")


async def get_all(conn) -> dict:
    rows = await fetch_all(conn, "select key, value from settings")
    values = dict(DEFAULTS)
    values["web_front"] = config.settings.web_front  # a fork can pick its front page in the environment
    values.update({r["key"]: r["value"] for r in rows})
    if not values["sms_sender"]:
        values["sms_sender"] = config.settings.sms_sender
    return values


async def get(conn, key: str):
    return (await get_all(conn))[key]


async def set_many(conn, values: dict) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        await conn.execute(
            """insert into settings (key, value, updated_at) values (%s, %s, now())
               on conflict (key) do update set value = excluded.value, updated_at = now()""",
            (key, Jsonb(value)),
        )
