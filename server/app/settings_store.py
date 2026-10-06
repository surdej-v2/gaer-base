"""Instance settings edited in /admin/settings, /admin/consent and /admin/retention."""

from psycopg.types.json import Jsonb

from app import config
from app.db import fetch_all

DEFAULTS: dict = {
    "app_name": "Gær",
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
    "retention_last_run": None,
    "ai_default_monthly_tokens": 200_000,   # per user; 0 turns AI off for users without their own limit
}

PUBLIC_KEYS = ("app_name", "logo_key", "consent_version", "consent_title", "consent_text",
               "consent_accept", "consent_decline")


async def get_all(conn) -> dict:
    rows = await fetch_all(conn, "select key, value from settings")
    values = dict(DEFAULTS)
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
