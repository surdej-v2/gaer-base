from tests.helpers import SoftAuthenticator, full_user, otp_login, passkey_login, register_passkey


async def test_first_user_becomes_admin(make_client, owner_conn):
    first, second = make_client(), make_client()
    await otp_login(first, "12 34 56 78")
    await otp_login(second, "+45 87654321")
    roles = owner_conn.execute("select phone, role from users order by id").fetchall()
    assert roles == [("+4512345678", "admin"), ("+4587654321", "user")]
    actions = [r[0] for r in owner_conn.execute("select action from audit_log order by id").fetchall()]
    assert actions.count("admin.first_user") == 1


async def test_three_grace_logins_then_passkey_required(make_client, owner_conn):
    for expected_left in (2, 1, 0):
        client = make_client()
        body = await otp_login(client, "12345678")
        assert body == {"status": "logged_in", "grace_left": expected_left, "offer_passkey": True,
                        "became_admin": expected_left == 2}
        assert (await client.get("/api/me/sessions")).status_code == 200

    client = make_client()
    body = await otp_login(client, "12345678")
    assert body == {"status": "passkey_required", "reason": "grace_exhausted"}
    # The enroll session may only register a passkey.
    assert (await client.get("/api/me/sessions")).status_code == 403
    assert (await client.get("/api/auth/me")).json()["scope"] == "enroll"

    await register_passkey(client, SoftAuthenticator())
    assert (await client.get("/api/auth/me")).json()["scope"] == "full"
    assert (await client.get("/api/me/sessions")).status_code == 200

    events = [r[0] for r in owner_conn.execute("select event from auth_log order by id").fetchall()]
    assert events.count("grace.used") == 3
    assert "passkey.registered" in events


async def test_passkey_login_and_new_device(make_client, owner_conn):
    authenticator = await full_user(make_client(), "12345678")

    client = make_client()
    r = await passkey_login(client, authenticator)
    assert r.status_code == 200 and r.json() == {"status": "logged_in"}
    assert (await client.get("/api/auth/me")).json()["scope"] == "full"

    # New device: a one-time code only allows registering a new passkey and uses no grace login.
    grace_before = owner_conn.execute("select grace_logins_used from users").fetchone()[0]
    client = make_client()
    assert await otp_login(client, "12345678") == {"status": "passkey_required", "reason": "new_device"}
    await register_passkey(client, SoftAuthenticator())
    assert owner_conn.execute("select grace_logins_used from users").fetchone()[0] == grace_before
    assert owner_conn.execute("select count(*) from passkeys").fetchone()[0] == 2


async def test_unknown_passkey_is_rejected(client, owner_conn):
    r = await passkey_login(client, SoftAuthenticator())
    assert r.status_code == 400
    assert owner_conn.execute("select event, result from auth_log").fetchall() == [("passkey.login", "fail")]


async def test_code_is_hashed_single_use_and_locks_after_failures(client, owner_conn):
    r = await client.post("/api/auth/otp/start", json={"phone": "12345678"})
    code = r.json()["dev_code"]
    stored = owner_conn.execute("select code_hash from otp_codes").fetchone()[0]
    assert code not in stored and len(stored) == 64

    assert (await client.post("/api/auth/otp/verify", json={"phone": "12345678", "code": code})).status_code == 200
    assert (await client.post("/api/auth/otp/verify", json={"phone": "12345678", "code": code})).status_code == 400

    r = await client.post("/api/auth/otp/start", json={"phone": "12345678"})
    for _ in range(4):
        assert (await client.post("/api/auth/otp/verify", json={"phone": "12345678", "code": "000000"})).status_code == 400
    # Fifth failure (the earlier reuse counts too) locks the number.
    r = await client.post("/api/auth/otp/verify", json={"phone": "12345678", "code": "000000"})
    assert r.status_code == 429
    assert (await client.post("/api/auth/otp/start", json={"phone": "12345678"})).status_code == 429


async def test_otp_rate_limit_per_phone(client):
    for _ in range(5):
        assert (await client.post("/api/auth/otp/start", json={"phone": "12345678"})).status_code == 200
    assert (await client.post("/api/auth/otp/start", json={"phone": "12345678"})).status_code == 429


async def test_auth_log_masks_phone(client, owner_conn):
    await otp_login(client, "12345678")
    masked = {r[0] for r in owner_conn.execute("select phone_masked from auth_log").fetchall()}
    assert masked == {"+45 •••• ••78"}
    row = owner_conn.execute("select host(ip), device from auth_log limit 1").fetchone()
    assert row[0] == "203.0.113.7" and "Chrome" in row[1]


async def test_blocked_user_cannot_log_in(make_client, owner_conn):
    await otp_login(make_client(), "11111111")  # admin
    authenticator = await full_user(make_client(), "22222222")
    owner_conn.execute("update users set blocked_at = now() where phone = '+4522222222'")
    assert (await passkey_login(make_client(), authenticator)).status_code == 403


async def test_logout_revokes_session(client, owner_conn):
    await otp_login(client, "12345678")
    await client.post("/api/auth/logout")
    assert (await client.get("/api/auth/me")).json() == {"user": None}
    assert owner_conn.execute("select count(*) from auth_log where event = 'session.revoked'").fetchone()[0] == 1


async def test_state_changes_require_csrf_header(client):
    r = await client.post("/api/auth/otp/start", json={"phone": "12345678"}, headers={"x-gaer": ""})
    assert r.status_code == 403


async def test_invalid_phone(client):
    assert (await client.post("/api/auth/otp/start", json={"phone": "abc"})).status_code == 400


async def test_public_config_says_when_first_login_becomes_admin(make_client):
    first = make_client()
    assert (await first.get("/api/public/config")).json()["first_user"] is True
    assert (await otp_login(first, "11111111"))["became_admin"] is True
    second = make_client()
    assert (await second.get("/api/public/config")).json()["first_user"] is False
    assert (await otp_login(second, "22222222"))["became_admin"] is False


# ── BOOTSTRAP_ADMIN_PHONE ───────────────────────────────────────────────────

import dataclasses  # noqa: E402

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.security import InvalidPhone, bootstrap_admin_phone  # noqa: E402


@pytest.fixture
def bootstrap(monkeypatch):
    def set_phone(value):
        monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, bootstrap_admin_phone=value))
    return set_phone


async def test_bootstrap_phone_is_the_only_first_admin(make_client, bootstrap, owner_conn):
    bootstrap("+45 99 88 77 66")
    early = make_client()
    assert (await early.get("/api/public/config")).json()["first_user"] is False  # fresh instance not advertised
    assert "bliver administrator" not in (await early.get("/w/")).text
    assert (await otp_login(early, "11111111"))["became_admin"] is False

    owner = make_client()
    assert (await otp_login(owner, "99887766"))["became_admin"] is True
    roles = dict(owner_conn.execute("select phone, role from users").fetchall())
    assert roles == {"+4511111111": "user", "+4599887766": "admin"}
    detail = owner_conn.execute("select detail from audit_log where action = 'admin.first_user'").fetchone()[0]
    assert detail["via"] == "bootstrap_admin_phone"


async def test_bootstrap_phone_existing_user_is_promoted_once(make_client, bootstrap, owner_conn):
    await otp_login(make_client(), "11111111")            # becomes admin the old way
    bootstrap("99887766")
    await otp_login(make_client(), "99887766")            # an admin exists: stays a normal user
    assert owner_conn.execute("select role from users where phone = '+4599887766'").fetchone()[0] == "user"

    owner_conn.execute("update users set role = 'user' where phone = '+4511111111'")  # no admin left
    assert (await otp_login(make_client(), "99887766"))["became_admin"] is True
    assert owner_conn.execute("select role from users where phone = '+4599887766'").fetchone()[0] == "admin"
    change = owner_conn.execute("select before, after, detail from audit_log where action = 'role.changed'").fetchone()
    assert change[:2] == ({"role": "user"}, {"role": "admin"}) and change[2]["reason"] == "bootstrap_admin_phone"


def test_invalid_bootstrap_phone_is_rejected(bootstrap):
    bootstrap("not-a-number")
    with pytest.raises(InvalidPhone):
        bootstrap_admin_phone()
