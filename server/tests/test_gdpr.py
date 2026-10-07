"""Self-service GDPR: export own data, change phone number, delete own account."""

import io
import json
import zipfile

from tests.helpers import full_user, otp_login


async def _export(client) -> tuple[dict, zipfile.ZipFile]:
    r = await client.get("/api/me/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return json.loads(z.read("data.json")), z


async def test_export_contains_everything_including_files(client, owner_conn):
    await otp_login(client, "12345678")
    await client.post("/api/files", files={"file": ("billede.png", b"\x89PNG data", "image/png")})
    await client.post("/api/clips", json={"title": "Tilbud", "body": "1.500 kr.", "source": "extension"})
    await client.post("/api/bridge-consent", json={"domain": "dr.dk"})
    data, z = await _export(client)
    assert data["user"]["phone"] == "+4512345678"
    assert data["clips"][0]["title"] == "Tilbud"
    assert data["bridge_consents"][0]["domain"] == "dr.dk"
    assert data["files"][0]["filename"] == "billede.png"
    assert z.read(next(n for n in z.namelist() if n.startswith("files/"))) == b"\x89PNG data"
    assert "LÆS MIG.txt" in z.namelist()
    row = owner_conn.execute("select detail from audit_log where action = 'gdpr.export'").fetchone()[0]
    assert row["by_self"] is True


async def test_delete_own_account(make_client, owner_conn):
    admin = make_client()
    await full_user(admin, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    await user.post("/api/files", files={"file": ("a.txt", b"x", "text/plain")})
    await user.post("/api/clips", json={"title": "Mit klip", "body": "x"})
    await user.post("/api/me/gdpr", json={"kind": "access", "message": "mit nummer er 22222222"})

    assert (await user.post("/api/me/delete", json={"confirm_phone": "99999999"})).status_code == 400
    r = await user.post("/api/me/delete", json={"confirm_phone": "22 22 22 22"})
    assert r.status_code == 200 and r.json()["files"] == 1
    assert "gaer_session=" in r.headers["set-cookie"]

    assert owner_conn.execute("select count(*) from users where phone = '+4522222222'").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from clips").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from files").fetchone()[0] == 0
    assert owner_conn.execute("select contact, message from gdpr_requests").fetchone() == ("slettet", "")
    assert owner_conn.execute("select count(*) from auth_log where phone_masked like '%22'").fetchone()[0] == 0
    erasure = owner_conn.execute("select actor_user_id, detail from audit_log where action = 'gdpr.erasure'").fetchone()
    assert erasure[0] is None and erasure[1]["by_self"] is True and erasure[1]["reason"] == "self_service"
    assert (await user.get("/api/auth/me")).json() == {"user": None}


async def test_last_admin_cannot_delete_own_account(client):
    await otp_login(client, "11111111")
    r = await client.post("/api/me/delete", json={"confirm_phone": "11111111"})
    assert r.status_code == 409


async def test_change_phone_with_code_to_new_number(make_client, owner_conn):
    me, other_device = make_client(), make_client()
    await otp_login(me, "11111111")
    await otp_login(other_device, "11111111")
    await otp_login(make_client(), "33333333")

    assert (await me.post("/api/me/phone/start", json={"phone": "33333333"})).status_code == 409   # taken
    assert (await me.post("/api/me/phone/start", json={"phone": "11111111"})).status_code == 400   # same

    code = (await me.post("/api/me/phone/start", json={"phone": "44 44 44 44"})).json()["dev_code"]
    # A phone-change code cannot be used to log in as the new number.
    r = await make_client().post("/api/auth/otp/verify", json={"phone": "44444444", "code": code})
    assert r.status_code == 400
    assert (await me.post("/api/me/phone/verify", json={"phone": "44444444", "code": "000000"})).status_code == 400

    code = (await me.post("/api/me/phone/start", json={"phone": "44444444"})).json()["dev_code"]
    r = await me.post("/api/me/phone/verify", json={"phone": "44444444", "code": code})
    assert r.status_code == 200 and r.json()["phone"] == "+4544444444"
    assert (await me.get("/api/auth/me")).json()["user"]["phone"] == "+4544444444"
    assert (await other_device.get("/api/auth/me")).json() == {"user": None}      # logged out elsewhere
    change = owner_conn.execute("select before, after from audit_log where action = 'update' and table_name = 'users'"
                                " and after ? 'phone'").fetchone()
    assert change == ({"phone": "+4511111111"}, {"phone": "+4544444444"})


async def test_admin_rectifies_phone(make_client, owner_conn):
    admin = make_client()
    await full_user(admin, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    assert (await admin.patch(f"/api/admin/users/{user_id}/phone", json={"phone": "11111111"})).status_code == 409
    assert (await admin.patch(f"/api/admin/users/{user_id}/phone", json={"phone": "55555555"})).status_code == 200
    assert owner_conn.execute("select phone from users where id = %s", (user_id,)).fetchone()[0] == "+4555555555"
    assert (await user.get("/api/auth/me")).json() == {"user": None}
    detail = owner_conn.execute("select detail from audit_log where action = 'update' and row_id = %s"
                                " and after ? 'phone'", (str(user_id),)).fetchone()[0]
    assert detail["reason"] == "rectification"


async def test_reapply_erasures_after_restore(make_client, owner_conn):
    from tests.conftest import OWNER_URL
    from app.reapply_erasures import erased_ids_since, reapply_ids

    await full_user(make_client(), "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    user_id = (await user.get("/api/auth/me")).json()["user"]["id"]
    snapshot = owner_conn.execute("select id, phone, role, created_at from users where id = %s", (user_id,)).fetchone()

    await user.post("/api/me/delete", json={"confirm_phone": "22222222"})
    ids = erased_ids_since(OWNER_URL, "2000-01-01T00:00:00Z")
    assert ids == [user_id]

    # Simulate a restore from a backup taken before the erasure: the person is back.
    owner_conn.execute("insert into users (id, phone, role, created_at) values (%s, %s, %s, %s)", snapshot)
    assert await reapply_ids(ids, dry_run=True) == 0
    assert owner_conn.execute("select count(*) from users where id = %s", (user_id,)).fetchone()[0] == 1

    assert await reapply_ids(ids) == 0
    assert owner_conn.execute("select count(*) from users where id = %s", (user_id,)).fetchone()[0] == 0
    assert await reapply_ids(ids) == 0   # safe to run twice
