"""Impersonation: test accounts, the separate impersonation cookie and audit with the admin as actor."""

from tests.helpers import full_user, otp_login

ADMIN_APP = {"x-gaer-app": "admin"}


async def _admin(make_client, phone="11111111"):
    client = make_client()
    await full_user(client, phone)
    return client


async def _me(client, **headers):
    return (await client.get("/api/auth/me", headers=headers)).json()


async def test_only_admins_manage_test_accounts(make_client):
    await _admin(make_client)
    user = make_client()
    await otp_login(user, "22222222")
    assert (await user.get("/api/impersonation/users")).status_code == 403
    assert (await user.post("/api/impersonation/users", json={"display_name": "X", "role": "user"})).status_code == 403


async def test_create_presets_and_list(make_client, owner_conn):
    admin = await _admin(make_client)
    r = await admin.post("/api/impersonation/users/presets")
    assert r.status_code == 201
    assert [u["display_name"] for u in r.json()["created"]] == ["Test Bruger", "Test Admin"]
    assert (await admin.post("/api/impersonation/users/presets")).json()["created"] == []  # idempotent

    users = (await admin.get("/api/impersonation/users")).json()["users"]
    assert users[0]["handle"] == f"test-{users[0]['id']}@impersonation.invalid"
    listed = (await admin.get("/api/admin/users")).json()["items"]
    assert sum(u["impersonation_only"] for u in listed) == 2
    assert owner_conn.execute("select count(*) from audit_log where action = 'impersonation.user.create'").fetchone()[0] == 2

    # Another admin neither sees nor uses them.
    other = make_client()
    await full_user(other, "33333333")
    owner_conn.execute("update users set role = 'admin' where phone = '+4533333333'")
    assert (await other.get("/api/impersonation/users")).json()["users"] == []
    assert not any(u["impersonation_only"] for u in (await other.get("/api/admin/users")).json()["items"])
    assert (await other.post("/api/impersonation/start", json={"user_id": users[0]["id"]})).status_code == 404


async def test_app_runs_as_test_account_admin_stays_real(make_client, owner_conn):
    admin = await _admin(make_client)
    test = (await admin.post("/api/impersonation/users", json={"display_name": "Test Kasserer", "role": "user"})).json()
    r = await admin.post("/api/impersonation/start", json={"user_id": test["id"]})
    assert r.status_code == 200 and "gaer_imp" in r.cookies

    me = await _me(admin)
    assert me["user"]["id"] == test["id"] and me["impersonator"]["id"] != test["id"]
    assert (await _me(admin, **ADMIN_APP))["user"]["role"] == "admin"
    assert (await admin.get("/api/admin/users")).status_code == 200  # /api/admin is always the real admin
    assert (await admin.get("/api/impersonation/status")).json()["user"]["id"] == test["id"]

    # A change made as the test account is audited with the admin as actor.
    r = await admin.post("/api/clips", json={"title": "Fra testkontoen"})
    assert r.status_code == 200, r.text
    actor, detail = owner_conn.execute(
        "select actor_user_id, detail from audit_log where table_name = 'clips' and action = 'create'").fetchone()
    assert actor == me["impersonator"]["id"] and detail["as_user_id"] == test["id"]
    assert owner_conn.execute("select user_id from clips").fetchone()[0] == test["id"]

    # A test account never gets a passkey.
    assert (await admin.post("/api/auth/passkey/register/options")).status_code == 403

    assert (await admin.delete(f"/api/impersonation/users/{test['id']}")).status_code == 409
    assert (await admin.post("/api/impersonation/stop")).status_code == 200
    assert (await _me(admin))["user"]["role"] == "admin"
    assert (await admin.delete(f"/api/impersonation/users/{test['id']}")).status_code == 200
    actions = [r[0] for r in owner_conn.execute("select action from audit_log where action like 'impersonation.%' order by id")]
    assert actions == ["impersonation.user.create", "impersonation.start", "impersonation.stop", "impersonation.user.delete"]


async def test_logout_while_impersonating_keeps_admin_session(make_client):
    admin = await _admin(make_client)
    test = (await admin.post("/api/impersonation/users", json={"display_name": "T", "role": "admin"})).json()
    await admin.post("/api/impersonation/start", json={"user_id": test["id"]})
    assert (await admin.post("/api/auth/logout")).status_code == 200
    me = await _me(admin)
    assert me["user"]["role"] == "admin" and me["impersonator"] is None
    assert (await admin.get("/api/impersonation/status")).json()["user"] is None


async def test_test_admin_does_not_count_as_admin(make_client):
    admin = await _admin(make_client)
    await admin.post("/api/impersonation/users", json={"display_name": "Test Admin", "role": "admin"})
    me = (await _me(admin))["user"]["id"]
    assert (await admin.patch(f"/api/admin/users/{me}/role", json={"role": "user"})).status_code == 409
    stats = (await admin.get("/api/admin/analytics")).json()
    assert stats["users"]["total"] == 1


async def test_impersonation_cookie_without_admin_session_is_ignored(make_client):
    admin = await _admin(make_client)
    test = (await admin.post("/api/impersonation/users", json={"display_name": "T", "role": "user"})).json()
    r = await admin.post("/api/impersonation/start", json={"user_id": test["id"]})
    stranger = make_client()
    stranger.cookies.set("gaer_imp", r.cookies["gaer_imp"])
    assert (await _me(stranger))["user"] is None
    user = make_client()
    await otp_login(user, "22222222")
    user.cookies.set("gaer_imp", r.cookies["gaer_imp"])
    assert (await _me(user))["user"]["phone"] == "+4522222222"


async def test_test_account_never_gets_a_phone_or_deletes_itself(make_client):
    admin = await _admin(make_client)
    test = (await admin.post("/api/impersonation/users", json={"display_name": "T", "role": "user"})).json()
    await admin.post("/api/impersonation/start", json={"user_id": test["id"]})
    assert (await admin.post("/api/me/phone/start", json={"phone": "44444444"})).status_code == 403
    assert (await admin.post("/api/me/delete", json={"confirm_phone": test["handle"]})).status_code == 403
    r = await admin.patch(f"/api/admin/users/{test['id']}/phone", json={"phone": "44444444"})
    assert r.status_code == 409
    assert (await _me(admin, **ADMIN_APP))["user"]["role"] == "admin"
