"""Features and rings: who sees what, usage, admin changes, import from the catalog and log rules."""

import psycopg
import pytest

from tests.helpers import full_user, otp_login


async def _admin_and_user(make_client):
    admin = make_client()
    await full_user(admin, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    return admin, user


def _enabled(data: dict) -> dict:
    return {f["key"]: f["enabled"] for f in data["features"]}


async def test_ring_and_level_decide_who_sees_a_feature(make_client, owner_conn):
    admin, user = await _admin_and_user(make_client)
    r = await admin.post("/api/admin/features", json={"key": "ai-klip", "title": "AI-opsummering", "area": "bruger"})
    assert r.status_code == 201 and r.json()["ring"] == 1

    data = (await user.get("/api/features")).json()
    assert data["level"] == 4 and _enabled(data)["ai-klip"] is False and _enabled(data)["klip"] is True
    assert _enabled((await make_client().get("/api/features")).json())["ai-klip"] is False  # anonymous

    # The user opts in to early features; an admin can also set and reset the level.
    assert (await user.put("/api/features/level", json={"ring": 1})).status_code == 200
    assert _enabled((await user.get("/api/features")).json())["ai-klip"] is True
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    assert (await admin.delete(f"/api/admin/features/levels/{user_id}")).status_code == 200
    assert (await user.get("/api/features")).json()["level"] == 4
    await admin.put(f"/api/admin/features/levels/{user_id}", json={"ring": 2})
    assert (await admin.get("/api/admin/features")).json()["levels"][0]["ring"] == 2
    assert (await admin.get("/api/admin/users")).json()["items"][1]["feature_ring"] == 2

    # Moving the feature to ring 3 shows it to everyone.
    r = await admin.patch("/api/admin/features/ai-klip", json={"ring": 3, "observe": True})
    assert r.json() == {"key": "ai-klip", "ring": 3, "observe": True}
    assert _enabled((await user.get("/api/features")).json())["ai-klip"] is True
    row = owner_conn.execute("select before, after from audit_log where action = 'feature.ring'").fetchone()
    assert row == ({"ring": 1, "observe": False}, {"ring": 3, "observe": True})

    assert (await user.patch("/api/admin/features/ai-klip", json={"ring": 4})).status_code == 403
    assert (await admin.delete("/api/admin/features/klip")).status_code == 409  # from the code
    assert (await admin.delete("/api/admin/features/ai-klip")).status_code == 200


async def test_usage_is_counted_once_a_minute(make_client):
    admin, user = await _admin_and_user(make_client)
    r = await user.post("/api/features/usage", json={"keys": ["klip", "filer", "findes-ikke"]})
    assert r.json() == {"recorded": 2}
    assert (await user.post("/api/features/usage", json={"keys": ["klip"]})).json() == {"recorded": 0}
    await admin.post("/api/features/usage", json={"keys": ["klip"]})

    items = {i["key"]: i for i in (await admin.get("/api/admin/features")).json()["items"]}
    assert items["klip"]["uses_30d"] == 2 and items["klip"]["users_30d"] == 2
    usage = (await admin.get("/api/admin/features/klip/usage")).json()
    assert {u["phone"] for u in usage} == {"+4511111111", "+4522222222"}
    top = (await admin.get("/api/admin/analytics")).json()["features"]
    assert top[0]["key"] == "klip" and top[0]["uses"] == 2


async def test_import_missing_from_the_catalog(make_client, owner_conn):
    admin, _ = await _admin_and_user(make_client)
    assert (await admin.get("/api/admin/features")).json()["missing"] == []
    owner_conn.execute("delete from features where key in ('gaer-sync', 'heatmaps')")
    owner_conn.execute("update features set ring = 2 where key = 'klip'")
    missing = (await admin.get("/api/admin/features")).json()["missing"]
    assert [m["key"] for m in missing] == ["heatmaps", "gaer-sync"]

    assert (await admin.post("/api/admin/features/import")).json() == {"created": ["heatmaps", "gaer-sync"]}
    rings = dict(owner_conn.execute("select key, ring from features").fetchall())
    assert rings["heatmaps"] == 1 and rings["gaer-sync"] == 1 and rings["klip"] == 2  # admin's ring is kept


async def test_feature_usage_is_append_only_and_purged(make_client, app_conn, purge_conn):
    admin, user = await _admin_and_user(make_client)
    await user.post("/api/features/usage", json={"keys": ["klip"]})
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("delete from feature_usage")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("update feature_usage set feature_key = 'x'")
    user_id = (await admin.get("/api/admin/users")).json()["items"][1]["id"]
    r = await admin.post(f"/api/admin/retention/users/{user_id}", json={"logs": ["analytics"]})
    assert r.json()["deleted"]["feature_usage"] == 1
    assert purge_conn.execute("select count(*) from feature_usage").fetchone()[0] == 0
