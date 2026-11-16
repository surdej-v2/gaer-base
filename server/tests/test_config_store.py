"""Konfiguration (/admin/config, app/config_store.py): database values over the environment, secrets written and
never read, endpoints that cannot take a stored key to a new host, login rules that can only be tightened, and every
setting either in /admin or deliberately kept in the environment."""

import dataclasses

import pytest

from app import config, config_store, mail
from app import config_registry as reg
from tests.helpers import full_user, stepup

API = "/api/admin/config"


@pytest.fixture(autouse=True)
def reset_config(owner_conn):
    yield
    owner_conn.execute("truncate config_values, config_secrets")
    config._overrides = {}
    config.settings = config.env_settings
    config_store.invalidate()


async def _admin(make_client, phone="61111111"):
    c = make_client()
    auth = await full_user(c, phone)  # the first user: super admin, with a fresh second factor
    return c, auth


async def _put(client, auth, group, values=None, secrets=None):
    body = {"values": values or {}, "secrets": secrets or {}}
    r = await client.put(f"{API}/{group}", json=body)
    if r.status_code == 403 and r.headers.get("x-gaer-error") in ("stepup_required", "mfa_required"):
        await stepup(client, auth)
        r = await client.put(f"{API}/{group}", json=body)
    return r


def _item(overview: dict, key: str) -> dict:
    return next(i for g in overview["groups"] for i in g["items"] if i["key"] == key)


def test_every_setting_is_in_admin_or_kept_in_the_environment():
    fields = set(config.Settings.__dataclass_fields__)
    assert not set(reg.KEYS) & set(reg.ENV_ONLY)
    assert fields == set(reg.KEYS) | set(reg.ENV_ONLY), fields ^ (set(reg.KEYS) | set(reg.ENV_ONLY))
    for key, k in reg.KEYS.items():
        assert k.group in reg.GROUPS
        if k.endpoint:
            assert reg.KEYS[k.endpoint].kind == "url" and k.secret
    # What must never be set from a browser.
    for key in ("secret_key", "secret_box_keys", "totp_keys", "database_url", "demo_mode", "webhook_allow_private",
                "yubico_roots", "rp_id", "origin", "s3_secret_key", "stalwart_master_secret", "ingest_status_table"):
        assert key in reg.ENV_ONLY


async def test_a_value_overrides_the_environment_and_can_be_removed_again(make_client, owner_conn):
    admin, auth = await _admin(make_client)
    assert config.settings.max_upload_mb == 25
    r = await _put(admin, auth, "files", {"max_upload_mb": 50, "direct_upload": False})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["direct_upload", "max_upload_mb"]
    assert config.settings.max_upload_mb == 50 and config.settings.direct_upload is False
    item = _item((await admin.get(API)).json(), "max_upload_mb")
    assert item["source"] == "db" and item["value"] == 50 and item["fallback"] == 25

    row = owner_conn.execute("select before, after from audit_log where action = 'config.changed'").fetchone()
    assert row == ({"direct_upload": True, "max_upload_mb": 25}, {"direct_upload": False, "max_upload_mb": 50})

    r = await _put(admin, auth, "files", {"max_upload_mb": None})
    assert r.status_code == 200 and config.settings.max_upload_mb == 25
    assert _item((await admin.get(API)).json(), "max_upload_mb")["source"] == "default"


async def test_values_are_checked_and_login_rules_can_only_be_tightened(make_client, monkeypatch):
    admin, auth = await _admin(make_client)
    assert (await _put(admin, auth, "files", {"max_upload_mb": 500})).status_code == 422   # Cloudflare's 100 MB
    assert (await _put(admin, auth, "files", {"max_upload_mb": "50"})).status_code == 422
    assert (await _put(admin, auth, "login", {"grace_logins": 10})).status_code == 422
    assert (await _put(admin, auth, "login", {"stepup_minutes": 60})).status_code == 422
    assert (await _put(admin, auth, "login", {"session_days": 365})).status_code == 422
    assert (await _put(admin, auth, "files", {"grace_logins": 1})).status_code == 422      # another group's key
    assert (await _put(admin, auth, "mail", {"demo_mode": True})).status_code == 422       # never in /admin
    assert (await _put(admin, auth, "mail", {"lettermint_api_url": "http://lettermint.example"})).status_code == 422
    r = await _put(admin, auth, "login", {"grace_logins": 1, "stepup_minutes": 5})
    assert r.status_code == 200 and config.settings.grace_logins == 1 and config.settings.stepup_minutes == 5

    # In production the robot check cannot be switched off, nor codes sent to the console.
    monkeypatch.setattr(config, "env_settings", dataclasses.replace(config.env_settings, environment="production"))
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, environment="production"))
    assert (await _put(admin, auth, "login", {"captcha": "off"})).status_code == 422
    assert (await _put(admin, auth, "mail", {"mail_provider": "console"})).status_code == 422
    assert (await _put(admin, auth, "sms", {"sms_provider": "console"})).status_code == 422


async def test_a_secret_is_encrypted_used_and_never_shown(make_client, owner_conn):
    admin, auth = await _admin(make_client)
    r = await _put(admin, auth, "mail", {"mail_provider": "lettermint", "mail_from": "login@kunde.dk"},
                   {"lettermint_api_token": "lm_project_hemmelig_123"})
    assert r.status_code == 200, r.text
    assert r.json()["secrets_set"] == ["lettermint_api_token"]
    assert config.settings.lettermint_api_token == "lm_project_hemmelig_123"
    assert mail.missing_config() == []

    overview = await admin.get(API)
    assert "lm_project_hemmelig_123" not in overview.text
    item = _item(overview.json(), "lettermint_api_token")
    assert item["set"] is True and item["source"] == "db" and "value" not in item

    enc, host = owner_conn.execute("select value_enc, bound_host from config_secrets").fetchone()
    assert b"hemmelig" not in bytes(enc) and host == "api.lettermint.co"
    logged = str(owner_conn.execute("select before, after, detail from audit_log where action = 'config.changed'")
                 .fetchall())
    assert "hemmelig" not in logged and "lettermint_api_token" in logged

    r = await _put(admin, auth, "mail", secrets={"lettermint_api_token": None})
    assert r.json()["secrets_removed"] == ["lettermint_api_token"]
    assert config.settings.lettermint_api_token is None


async def test_a_new_endpoint_host_never_gets_a_stored_or_environment_key(make_client, monkeypatch):
    admin, auth = await _admin(make_client)
    await _put(admin, auth, "mail", secrets={"lettermint_api_token": "lm_project_1"})
    r = await _put(admin, auth, "mail", {"lettermint_api_url": "https://evil.example/v1"})
    assert r.json()["secrets_cleared"] == ["lettermint_api_token"]
    assert config.settings.lettermint_api_token is None
    # Same host, other path: the key stays.
    await _put(admin, auth, "mail", {"lettermint_api_url": None}, {"lettermint_api_token": "lm_project_2"})
    r = await _put(admin, auth, "mail", {"lettermint_api_url": "https://api.lettermint.co/v2"})
    assert r.json()["secrets_cleared"] == [] and config.settings.lettermint_api_token == "lm_project_2"

    # A key from the environment is only used against the environment's endpoint.
    monkeypatch.setattr(config, "env_settings", dataclasses.replace(config.env_settings, ai_api_key="env-ai-key"))
    await _put(admin, auth, "ai", {"ai_model": "Mistral-Nemo"})
    assert config.settings.ai_api_key == "env-ai-key" and config.settings.ai_model == "Mistral-Nemo"
    await _put(admin, auth, "ai", {"ai_base_url": "https://ai.other.example/v1"})
    assert config.settings.ai_api_key is None
    item = _item((await admin.get(API)).json(), "ai_api_key")
    assert item["set"] is False and item["ignored"]
    await _put(admin, auth, "ai", {"ai_base_url": None})
    assert config.settings.ai_api_key == "env-ai-key"


async def test_groups_follow_the_admin_roles(make_client, owner_conn):
    await _admin(make_client)
    ops = make_client()
    auth = await full_user(ops, "62222222")
    owner_conn.execute("update users set role = 'admin', admin_roles = '{drift}' where phone = '+4562222222'")
    groups = [g["key"] for g in (await ops.get(API)).json()["groups"]]
    assert "mail" in groups and "sms" in groups and "login" not in groups and "ai" not in groups
    assert (await _put(ops, auth, "login", {"grace_logins": 1})).status_code == 403
    assert (await _put(ops, auth, "sms", {"sms_sender": "Kunde"})).status_code == 200
    assert config.settings.sms_sender == "Kunde"

    support = make_client()
    await full_user(support, "63333333")
    owner_conn.execute("update users set role = 'admin', admin_roles = '{support}' where phone = '+4563333333'")
    assert (await support.get(API)).status_code == 403


async def test_import_copies_the_environment_once(make_client, owner_conn, monkeypatch):
    admin, auth = await _admin(make_client)
    monkeypatch.setenv("MAIL_FROM_NAME", "Kunde")
    monkeypatch.setenv("LETTERMINT_API_TOKEN", "lm_from_env")
    monkeypatch.setattr(config, "env_settings", dataclasses.replace(
        config.env_settings, mail_from_name="Kunde", lettermint_api_token="lm_from_env"))
    assert (await admin.get(API)).json()["importable"] >= 2
    r = await admin.post(f"{API}/import")
    if r.status_code == 403:
        await stepup(admin, auth)
        r = await admin.post(f"{API}/import")
    assert r.status_code == 200, r.text
    copied = r.json()["copied"]
    assert "mail_from_name" in copied and "lettermint_api_token" in copied
    assert "rp_id" not in copied and "secret_key" not in copied
    assert owner_conn.execute("select value from config_values where key = 'mail_from_name'").fetchone() == ("Kunde",)
    assert owner_conn.execute("select bound_host from config_secrets where key = 'lettermint_api_token'").fetchone() \
        == ("api.lettermint.co",)
    assert (await admin.post(f"{API}/import")).json()["copied"] == []   # nothing left to copy
    item = _item((await admin.get(API)).json(), "lettermint_api_token")
    assert item["source"] == "db" and item["set"]


async def test_other_processes_pick_up_a_change_and_skip_values_that_no_longer_pass(owner_conn):
    owner_conn.execute("""insert into config_values (key, value) values ('unzip_max_files', '7'),
                                                                         ('grace_logins', '9')""")
    config_store.invalidate()
    await config_store.refresh()
    assert config.settings.unzip_max_files == 7
    assert config.settings.grace_logins == 3   # 9 is beyond the floor: the environment's value is used
