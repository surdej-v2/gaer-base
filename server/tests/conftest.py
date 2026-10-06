"""Tests run against a real Postgres. A fresh database is created with the same role
layout as production (<db>_app, <db>_purge), so the log grants are tested for real.

    TEST_DATABASE_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest server
"""

import os
import uuid
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres@localhost:5432/postgres")
DB_NAME = f"gaer_test_{uuid.uuid4().hex[:8]}"
ROLE_PASSWORD = "test-only-password"


def _url(user: str, password: str | None, database: str) -> str:
    parts = urlparse(ADMIN_URL)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    auth = f"{user}:{password}@" if password else f"{user}@"
    return urlunparse(parts._replace(netloc=auth + host, path="/" + database))


def _setup_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'create database "{DB_NAME}"')
        for suffix in ("app", "purge"):
            role = f"{DB_NAME}_{suffix}"
            conn.execute(f"create role \"{role}\" login password '{ROLE_PASSWORD}'")
            conn.execute(f'grant connect on database "{DB_NAME}" to "{role}"')


def _drop_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'drop database if exists "{DB_NAME}" with (force)')
        for suffix in ("app", "purge"):
            conn.execute(f'drop role if exists "{DB_NAME}_{suffix}"')


_setup_database()
OWNER_URL = urlunparse(urlparse(ADMIN_URL)._replace(path="/" + DB_NAME))
APP_URL = _url(f"{DB_NAME}_app", ROLE_PASSWORD, DB_NAME)
PURGE_URL = _url(f"{DB_NAME}_purge", ROLE_PASSWORD, DB_NAME)

os.environ.update({
    "DATABASE_URL": APP_URL,
    "PURGE_DATABASE_URL": PURGE_URL,
    "MIGRATION_DATABASE_URL": OWNER_URL,
    "STORAGE_BACKEND": "memory",
    "SMS_PROVIDER": "console",
    "RUN_MAINTENANCE": "0",
    "RP_ID": "localhost",
    "ORIGIN": "http://localhost:5173",
    "SECRET_KEY": "test-secret",
    "STATIC_DIR": "/nonexistent",
})

from app import config  # noqa: E402

config.reload()

from app import db, migrate  # noqa: E402
from app.main import app  # noqa: E402

migrate.migrate(verbose=False)


def pytest_sessionfinish(session, exitstatus):
    _drop_database()


@pytest.fixture(scope="session", autouse=True)
async def pools():
    await db.open_pools()
    yield
    await db.close_pools()


@pytest.fixture(autouse=True)
def clean_database():
    """Each test starts empty, but with the schema in place."""
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        conn.execute("""truncate users, otp_codes, passkeys, sessions, webauthn_challenges, auth_log, audit_log,
                        files, settings, gdpr_requests, consents, analytics_events, clips, bridge_consents, ai_usage
                        restart identity cascade""")
    from app.storage import get_storage
    get_storage().objects.clear()
    yield


@pytest.fixture
def owner_conn():
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def app_conn():
    with psycopg.connect(APP_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def purge_conn():
    with psycopg.connect(PURGE_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def make_client():
    import httpx

    clients = []

    def factory(user_agent: str = "Mozilla/5.0 (Macintosh; Mac OS X) Chrome/130 Safari/537.36"):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 5000)),
                                   base_url="http://localhost",
                                   headers={"x-gaer": "1", "user-agent": user_agent})
        clients.append(client)
        return client

    yield factory


@pytest.fixture
async def client(make_client):
    return make_client()
