import io
import json
import zipfile

from tests.helpers import full_user, otp_login


async def _admin_and_user(make_client):
    admin = make_client()
    await full_user(admin, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    return admin, user


async def test_admin_requires_admin_role(make_client):
    admin, user = await _admin_and_user(make_client)
    assert (await user.get("/api/admin/users")).status_code == 403
    r = await admin.get("/api/admin/users")
    assert r.status_code == 200 and r.json()["total"] == 2


async def test_admin_reads_are_audited(make_client, owner_conn):
    admin, _ = await _admin_and_user(make_client)
    await admin.get("/api/admin/logs/auth?event=otp.sent")
    row = owner_conn.execute("select detail from audit_log where action = 'admin.read'").fetchone()
    assert row[0]["path"] == "/api/admin/logs/auth" and row[0]["query"] == "event=otp.sent"


async def test_role_change_and_last_admin_protection(make_client, owner_conn):
    admin, user = await _admin_and_user(make_client)
    users = (await admin.get("/api/admin/users")).json()["items"]
    admin_id, user_id = users[0]["id"], users[1]["id"]

    assert (await admin.patch(f"/api/admin/users/{admin_id}/role", json={"role": "user"})).status_code == 409
    assert (await admin.patch(f"/api/admin/users/{user_id}/role", json={"role": "admin"})).status_code == 200
    row = owner_conn.execute("select before, after from audit_log where action = 'role.changed'").fetchone()
    assert row == ({"role": "user"}, {"role": "admin"})
    assert (await user.get("/api/admin/users")).status_code == 200


async def test_block_revokes_sessions(make_client):
    admin, user = await _admin_and_user(make_client)
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    assert (await admin.post(f"/api/admin/users/{user_id}/block")).status_code == 200
    assert (await user.get("/api/auth/me")).json() == {"user": None}
    assert (await admin.post(f"/api/admin/users/{user_id}/unblock")).status_code == 200


async def test_reset_passkeys(make_client, owner_conn):
    admin = make_client()
    await full_user(admin, "11111111")
    await full_user(make_client(), "22222222")
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    r = await admin.post(f"/api/admin/users/{user_id}/reset-passkeys")
    assert r.json()["deleted"] == 1
    assert owner_conn.execute("select count(*) from passkeys where user_id = %s", (user_id,)).fetchone()[0] == 0


async def test_log_search_by_full_phone_and_csv_export(make_client, owner_conn):
    admin, _ = await _admin_and_user(make_client)
    r = await admin.get("/api/admin/logs/auth", params={"phone": "22 22 22 22"})
    items = r.json()["items"]
    assert items and all(i["phone_masked"] == "+45 •••• ••22" for i in items)

    r = await admin.get("/api/admin/logs/audit.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "admin.first_user" in r.text
    assert owner_conn.execute("select count(*) from audit_log where action = 'log.exported'").fetchone()[0] == 1


async def test_retention_purge_uses_purge_user_and_is_logged(make_client, owner_conn):
    admin, _ = await _admin_and_user(make_client)
    owner_conn.execute("alter table auth_log disable trigger auth_log_no_update")
    owner_conn.execute("update auth_log set ts = now() - interval '400 days' where id = 1")
    owner_conn.execute("alter table auth_log enable trigger auth_log_no_update")

    r = await admin.post("/api/admin/retention/run")
    assert r.status_code == 200 and r.json()["deleted"]["auth_log"] == 1
    detail = owner_conn.execute("select detail from audit_log where action = 'log.purged'").fetchone()[0]
    assert detail["deleted"]["auth_log"] == 1 and detail["reason"] == "manual"

    stats = (await admin.get("/api/admin/retention")).json()
    assert {s["log"] for s in stats["logs"]} == {"auth_log", "audit_log", "analytics"}


async def test_purge_one_user(make_client, owner_conn):
    admin, _ = await _admin_and_user(make_client)
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    r = await admin.post(f"/api/admin/retention/users/{user_id}", json={"logs": ["auth_log"]})
    assert r.json()["deleted"]["auth_log"] > 0
    assert owner_conn.execute("select count(*) from auth_log where user_id = %s", (user_id,)).fetchone()[0] == 0


async def test_gdpr_access_and_erasure(make_client, owner_conn):
    admin, user = await _admin_and_user(make_client)
    await user.post("/api/files", files={"file": ("cv.pdf", b"%PDF-1.4", "application/pdf")})
    r = await user.post("/api/me/gdpr", json={"kind": "access"})
    request_id = r.json()["id"]

    listing = (await admin.get("/api/admin/gdpr")).json()
    assert listing[0]["kind"] == "access" and listing[0]["user_phone"] == "+4522222222"

    r = await admin.get(f"/api/admin/gdpr/{request_id}/export")
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    export = json.loads(z.read("data.json"))
    assert any(n.endswith("cv.pdf") and z.read(n) == b"%PDF-1.4" for n in z.namelist())
    assert export["user"]["phone"] == "+4522222222"
    assert export["files"][0]["filename"] == "cv.pdf"
    assert any(e["event"] == "otp.verified" for e in export["auth_log"])

    r = await user.post("/api/me/gdpr", json={"kind": "erasure"})
    erase_id = r.json()["id"]
    r = await admin.post(f"/api/admin/gdpr/{erase_id}/erase")
    assert r.status_code == 200, r.text
    assert owner_conn.execute("select count(*) from users where phone = '+4522222222'").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from files").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from auth_log where phone_masked like '%22'").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from audit_log where action = 'log.purged'").fetchone()[0] == 1
    # The record that the erasure happened survives the purge, without personal data.
    assert owner_conn.execute("select count(*) from audit_log where action = 'gdpr.erasure'").fetchone()[0] == 1
    assert {r[0] for r in owner_conn.execute("select contact from gdpr_requests").fetchall()} == {"slettet"}


async def test_public_gdpr_request(client):
    r = await client.post("/api/public/gdpr", json={"kind": "access", "contact": "mette@example.dk"})
    assert r.status_code == 200


async def test_settings_and_consent_texts(make_client, owner_conn):
    admin, _ = await _admin_and_user(make_client)
    r = await admin.put("/api/admin/settings", json={"app_name": "Bageriet", "sms_sender": "Bageriet",
                                                     "gdpr_email": "gdpr@bageriet.dk"})
    assert r.status_code == 200
    assert (await admin.get("/api/public/config")).json()["app_name"] == "Bageriet"

    texts = (await admin.get("/api/admin/consent")).json()["texts"]
    texts.pop("consent_version")
    r = await admin.put("/api/admin/consent", json={**texts, "consent_title": "Cookies?", "ask_again": True})
    assert r.status_code == 200
    config = (await admin.get("/api/public/config")).json()
    assert config["consent_title"] == "Cookies?" and config["consent_version"] == 2

    r = await admin.post("/api/admin/settings/logo", files={"file": ("logo.png", b"\x89PNG", "image/png")})
    assert r.status_code == 200
    assert (await admin.get("/api/public/logo")).content == b"\x89PNG"


async def test_admin_files(make_client):
    admin, user = await _admin_and_user(make_client)
    await user.post("/api/files", files={"file": ("a.txt", b"hello", "text/plain")})
    overview = (await admin.get("/api/admin/files")).json()
    assert overview["totals"] == {"files": 1, "bytes": 5}
    file_id = overview["items"][0]["id"]
    assert (await admin.delete(f"/api/admin/files/{file_id}")).status_code == 200
