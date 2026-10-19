"""App Stores: stored edits, defaults and the ids the Happy AI side panel guides send."""

from tests.helpers import full_user, otp_login


async def test_defaults_and_merge(make_client, owner_conn):
    admin = make_client()
    await full_user(admin, "11111111")
    r = (await admin.get("/api/admin/app-store")).json()
    assert r["data"] == {} and r["defaults"]["cfg"]["bundleId"] == "localhost.app"
    assert r["defaults"]["links"]["privacy"] == "https://localhost/privatliv"

    r = await admin.put("/api/admin/app-store", json={
        "cfg": {"name": "my-gaer", "primary": "#112233"},
        "texts": {"da": {"subtitle": "Klip på farten", "nope": "x"}},
        "links": {"ascAppId": "6449012345"},
        "manual": {"apple": {"privacy": True, "hack": True}},
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["cfg"] == {"name": "my-gaer", "primary": "#112233"}
    assert data["texts"] == {"da": {"subtitle": "Klip på farten"}}
    assert data["manual"] == {"apple": {"privacy": True}}

    # null resets an edited value; other values are kept
    data = (await admin.put("/api/admin/app-store", json={"texts": {"da": {"subtitle": None}}})).json()["data"]
    assert data["texts"] == {"da": {}} and data["links"] == {"ascAppId": "6449012345"}
    assert owner_conn.execute("select count(*) from audit_log where table_name = 'app_store'").fetchone()[0] == 2

    assert (await admin.put("/api/admin/app-store", json={"cfg": {"primary": "red"}})).status_code == 422
    assert (await admin.put("/api/admin/app-store", json={"other": 1})).status_code == 422


async def test_side_panel_accounts(make_client):
    admin = make_client()
    await full_user(admin, "11111111")
    body = {"accounts": {"apple": {"team_id": "A1B2C3D4E5", "issuer_id": "57246542-96fe-1a63-e053-0824d011072a", "key_id": "2X9R4HXF34"},
                         "google": {"developer_id": "1234567890123456789", "service_account": "eas-submit@my-gaer.iam.gserviceaccount.com"},
                         "expo": {"owner": "my-gaer", "project_id": "3f1c2a7e-9b4d-4e61-8a2f-5c0d9e7b1a42"}}}
    r = await admin.put("/api/admin/app-store", json=body)
    assert r.status_code == 200 and r.json()["data"]["accounts"] == body["accounts"]

    # Guides can send one account at a time without wiping the others.
    r = await admin.put("/api/admin/app-store", json={"accounts": {"expo": {"owner": "andet-firma"}}})
    assert r.json()["data"]["accounts"]["apple"]["team_id"] == "A1B2C3D4E5"
    assert r.json()["data"]["accounts"]["expo"]["project_id"] == body["accounts"]["expo"]["project_id"]

    for bad in ({"apple": {"team_id": "abc"}}, {"google": {"service_account": "x@gmail.com"}}, {"expo": {"token": "expo_secret"}}):
        assert (await admin.put("/api/admin/app-store", json={"accounts": bad})).status_code == 422

    user = make_client()
    await otp_login(user, "22222222")
    assert (await user.put("/api/admin/app-store", json=body)).status_code == 403
