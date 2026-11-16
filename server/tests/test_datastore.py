"""Datalager (/api/store, /api/admin/store, app/datastore.py): NoSQL collections and KV namespaces, private and shared,
revisions, major/minor releases approved by the RACI of the owning group, pinned majors that sunset, GDPR."""

from app import db, gdpr, jobs
from app.logs import RequestMeta
from tests.helpers import full_user, otp_login


async def _admins(make_client, n: int = 4):
    """n admins with passkeys (fresh second factor). The first is the super admin; the others have the role Data
    (so Fuld adgang does not open every group container for them)."""
    clients = []
    for i in range(n):
        c = make_client()
        await full_user(c, str(i + 1) * 8)
        clients.append(c)
    boss = clients[0]
    ids = sorted(u["id"] for u in (await boss.get("/api/admin/users")).json()["items"])
    for uid in ids[1:]:
        assert (await boss.patch(f"/api/admin/users/{uid}/role", json={"role": "admin"})).status_code == 200
        assert (await boss.put(f"/api/admin/users/{uid}/admin-roles", json={"roles": ["data"]})).status_code == 200
    return clients, ids


async def _group(boss, ids, name="Salg"):
    r = await boss.post("/api/admin/groups", json={"name": name})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    for uid in ids:
        assert (await boss.put(f"/api/admin/groups/{gid}/members/{uid}", json={"role": "member"})).status_code == 200
    return gid


def _actions(owner_conn, cid) -> list[str]:
    return [r[0] for r in owner_conn.execute(
        "select action from audit_log where table_name = 'store_containers' and row_id = %s order by id", (str(cid),))]


async def test_personal_kv_is_private_until_shared(make_client, owner_conn):
    (boss, two), ids = await _admins(make_client, 2)
    anna = make_client()
    await otp_login(anna, "55555555")
    r = await anna.post("/api/store", json={"kind": "kv", "slug": "noter", "name": "Mine noter"})
    assert r.status_code == 201, r.text
    assert r.json()["ref"] == "~noter" and r.json()["my_level"] == "manage"
    assert (await anna.put("/api/store/~noter/items/tema", json={"value": "mørk"})).status_code == 200
    assert (await anna.put("/api/store/~noter/items/pin", json={"value": 1234, "private": True})).status_code == 200
    assert (await anna.get("/api/store/~noter/items/tema")).json()["value"] == "mørk"
    cid = (await anna.get("/api/store/~noter")).json()["id"]

    # Nobody else sees it, not even an admin with Fuld adgang, neither in /api/store nor in /api/admin/store.
    assert (await boss.get(f"/api/store/{cid}")).status_code == 404
    assert (await boss.get(f"/api/admin/store/{cid}")).status_code == 404
    assert cid not in [c["id"] for c in (await boss.get("/api/admin/store")).json()["items"]]

    # Shared with a group: its members read, but not the private key; the group alone does nothing while private.
    gid = await _group(boss, [ids[1]], "Venner")
    anna_id = (await anna.get("/api/auth/me")).json()["user"]["id"]
    assert (await boss.put(f"/api/admin/groups/{gid}/members/{anna_id}", json={"role": "member"})).status_code == 200
    assert (await anna.put("/api/store/~noter/shares", json={"shares": [{"group_id": gid, "level": "read"}]})).status_code == 200
    assert (await two.get(f"/api/store/{cid}")).status_code == 404
    assert (await anna.patch("/api/store/~noter", json={"visibility": "shared"})).json()["visibility"] == "shared"
    listed = (await two.get(f"/api/store/{cid}/items")).json()
    assert [i["key"] for i in listed["items"]] == ["tema"]
    assert (await two.get(f"/api/store/{cid}/items/pin")).status_code == 404
    assert (await two.put(f"/api/store/{cid}/items/tema", json={"value": "lys"})).status_code == 403
    assert [c["id"] for c in (await two.get("/api/store")).json()["items"]] == [cid]
    assert _actions(owner_conn, cid) == ["create", "store.item_written", "store.item_written", "store.shares_changed", "update"]
    # Values are never in the audit log.
    assert "mørk" not in str(owner_conn.execute("select array_agg(detail::text) from audit_log").fetchone()[0])


async def test_nosql_filter_schema_and_revisions(make_client, owner_conn):
    (boss,), ids = await _admins(make_client, 1)
    gid = await _group(boss, ids)
    schema = {"type": "object", "required": ["navn"], "properties": {"navn": {"type": "string"}, "status": {"enum": ["aktiv", "lukket"]}}}
    r = await boss.post("/api/admin/store", json={"kind": "nosql", "group_id": gid, "slug": "kunder", "name": "Kunder",
                                                  "versioning": "revisions", "json_schema": schema})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = await boss.post("/api/store/kunder/items", json={"value": {"navn": "Nordlys", "status": "aktiv"}})
    assert r.status_code == 201 and r.json()["key"].startswith("d_")
    await boss.put("/api/store/kunder/items/havn", json={"value": {"navn": "Havnens Kaffe", "status": "lukket"}})
    r = await boss.put("/api/store/kunder/items/fejl", json={"value": {"status": "aktiv"}})
    assert r.status_code == 422 and "navn mangler" in r.json()["detail"]
    assert (await boss.put("/api/store/kunder/items/x", json={"value": "tekst"})).status_code == 400
    found = (await boss.get("/api/store/kunder/items", params={"filter": '{"status": "lukket"}'})).json()
    assert [i["key"] for i in found["items"]] == ["havn"] and found["total"] == 1
    assert (await boss.get("/api/store/kunder/items", params={"filter": "[1]"})).status_code == 400

    # Revisions: change, restore the first, delete — all kept.
    await boss.put("/api/store/kunder/items/havn", json={"value": {"navn": "Havnens Kaffe ApS", "status": "aktiv"}})
    revs = (await boss.get("/api/store/kunder/items/havn/revisions")).json()["items"]
    assert [r["rev"] for r in revs] == [2, 1]
    r = await boss.post("/api/store/kunder/items/havn/revisions/1/restore")
    assert r.json()["rev"] == 3 and r.json()["value"]["navn"] == "Havnens Kaffe"
    assert (await boss.delete("/api/store/kunder/items/havn")).status_code == 200
    assert (await boss.get("/api/store/kunder/items/havn")).status_code == 404
    revs = (await boss.get("/api/store/kunder/items/havn/revisions")).json()["items"]
    assert revs[0]["deleted"] and len(revs) == 4
    assert "store.item_restored" in _actions(owner_conn, cid)
    # A version pin means nothing without major/minor.
    assert (await boss.get("/api/store/kunder@1/items")).status_code == 400


async def test_semver_release_needs_the_raci(make_client, owner_conn):
    (boss, two, three, four), ids = await _admins(make_client)
    gid = await _group(boss, ids)
    raci = [{"user_id": ids[0], "roles": ["A"]}, {"user_id": ids[1], "roles": ["R"]}, {"user_id": ids[2], "roles": ["C"]},
            {"user_id": ids[3], "roles": ["I"]}]
    schema = {"type": "object", "properties": {"navn": {"type": "string"}, "telefon": {"type": "string"}}}
    r = await boss.post("/api/admin/store", json={"kind": "nosql", "group_id": gid, "slug": "kunder", "name": "Kunder",
                                                  "versioning": "semver", "json_schema": schema, "raci": raci})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # The I reads but cannot write; R writes the working copy.
    assert (await four.put("/api/store/kunder/items/k1", json={"value": {"navn": "A"}})).status_code == 403
    assert (await two.put("/api/store/kunder/items/k1", json={"value": {"navn": "Nordlys", "telefon": "1"}})).status_code == 200
    assert (await four.get("/api/store/kunder/items")).status_code == 404  # nothing released yet
    assert (await four.get("/api/store/kunder/items", params={"version": "draft"})).status_code == 403

    # 1.0 is a major: A and every C, never the proposer.
    r = await two.post(f"/api/store/kunder/versions", json={"note": "Første version"})
    assert r.status_code == 201, r.text
    rel = r.json()
    assert rel["version"] == "1.0" and rel["kind"] == "major" and rel["status"] == "pending"
    assert sorted(a["approver"]["id"] for a in rel["approvals"]) == [ids[0], ids[2]]
    assert (await two.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": True})).status_code == 403
    assert (await two.post("/api/store/kunder/versions", json={})).status_code == 409  # one pending at a time
    waiting = (await three.get("/api/admin/store")).json()["waiting"]
    assert [w["version"] for w in waiting] == ["1.0"]
    r = await three.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": True, "note": "ok"})
    assert r.json()["status"] == "pending"
    r = await boss.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": True})
    assert r.json()["status"] == "approved"
    assert (await four.get("/api/store/kunder")).json()["current_version"] == "1.0"
    assert [i["key"] for i in (await four.get("/api/store/kunder/items")).json()["items"]] == ["k1"]
    assert {j["payload"]["user_id"] for j in [{"payload": p} for (p,) in owner_conn.execute(
        "select payload from jobs where kind = 'store.mail'")]} >= {ids[0], ids[2], ids[1]}

    # A new document is a minor: one R or A (not the proposer).
    await two.put("/api/store/kunder/items/k2", json={"value": {"navn": "Havn"}})
    assert [i["key"] for i in (await four.get("/api/store/kunder/items")).json()["items"]] == ["k1"]  # still 1.0
    r = await two.post("/api/store/kunder/versions", json={"kind": "auto"})
    rel = r.json()
    assert rel["version"] == "1.1" and rel["kind"] == "minor"
    assert [a["approver"]["id"] for a in rel["approvals"]] == [ids[0]]
    assert (await boss.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": True})).json()["status"] == "approved"
    assert (await four.get("/api/store/kunder@1/items")).json()["version"] == "1.1"

    # Removing a property in the schema breaks readers: not a minor; a major that C rejects is closed.
    assert (await two.patch(f"/api/admin/store/{cid}", json={"json_schema": {"type": "object"}})).status_code == 403  # only A
    assert (await boss.patch(f"/api/admin/store/{cid}", json={"json_schema": {"type": "object", "properties": {"navn": {"type": "string"}}}})).status_code == 200
    r = await two.post("/api/store/kunder/versions", json={"kind": "minor"})
    assert r.status_code == 409 and "telefon" in r.json()["detail"]
    rel = (await two.post("/api/store/kunder/versions", json={"kind": "auto"})).json()
    assert rel["version"] == "2.0" and rel["breaking"] == ["Feltet telefon er fjernet"]
    assert (await three.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": False})).json()["status"] == "rejected"
    assert (await four.get("/api/store/kunder")).json()["current_version"] == "1.1"

    actions = _actions(owner_conn, cid)
    assert actions.count("store.release_proposed") == 3 and actions.count("store.release_approved") == 2
    assert "store.release_rejected" in actions and "update" in actions


async def test_kv_major_pin_and_sunset(make_client, owner_conn):
    (boss, two), ids = await _admins(make_client, 2)
    gid = await _group(boss, ids, "Produkt")
    raci = [{"user_id": ids[0], "roles": ["A", "R"]}, {"user_id": ids[1], "roles": ["R"]}]
    r = await boss.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "feature-config", "name": "Feature config",
                                                  "versioning": "semver", "major_rule": "a_only", "minor_rule": "auto",
                                                  "sunset_days": 30, "raci": raci})
    cid = r.json()["id"]
    await boss.put("/api/store/feature-config/items/checkout.max_items", json={"value": 50})
    # Proposed by the A with rule a_only: nobody else may approve, so one other eligible admin does (fallback).
    rel = (await boss.post("/api/store/feature-config/versions", json={})).json()
    assert rel["fallback"] and rel["approvals"] == []
    assert (await two.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": True})).json()["status"] == "approved"
    # Minor rule auto: an R's minor is approved at once. Same JSON type, so not breaking.
    await two.put("/api/store/feature-config/items/checkout.max_items", json={"value": 100})
    rel = (await two.post("/api/store/feature-config/versions", json={})).json()
    assert rel["version"] == "1.1" and rel["status"] == "approved"
    # A changed type is breaking → 2.0, approved by the A.
    await two.put("/api/store/feature-config/items/checkout.max_items", json={"value": "mange"})
    rel = (await two.post("/api/store/feature-config/versions", json={})).json()
    assert rel["kind"] == "major" and rel["breaking"] == ["checkout.max_items: type integer → string"]
    assert (await boss.post(f"/api/admin/store/versions/{rel['id']}/decide", json={"approve": True})).json()["status"] == "approved"

    assert (await two.get("/api/store/feature-config/items/checkout.max_items")).json()["value"] == "mange"
    old = (await two.get("/api/store/feature-config@1/items/checkout.max_items")).json()
    assert old["value"] == 100 and old["version"] == "1.1"
    assert (await two.get("/api/store/feature-config/items/checkout.max_items", params={"version": "1.0"})).json()["value"] == 50
    sunset = owner_conn.execute("select sunset_at - decided_at from store_releases where container_id = %s and major = 1 and minor = 1",
                                (cid,)).fetchone()[0]
    assert sunset.days in (29, 30)
    owner_conn.execute("update store_releases set sunset_at = now() - interval '1 minute' where container_id = %s and major = 1", (cid,))
    assert (await two.get("/api/store/feature-config@1/items")).status_code == 410
    async with db.connection() as conn:
        await jobs.enqueue(conn, "store.maintenance", {})
    await jobs.run_pending()
    assert owner_conn.execute("select count(*) from store_release_items i join store_releases r on r.id = i.release_id "
                              "where r.container_id = %s and r.major = 1", (cid,)).fetchone()[0] == 0


async def test_kv_ttl_and_none_versioning(make_client, owner_conn):
    (boss,), ids = await _admins(make_client, 1)
    gid = await _group(boss, ids, "Drift")
    r = await boss.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "job-cache", "name": "Job-cache",
                                                  "versioning": "none", "default_ttl_seconds": 3600})
    cid = r.json()["id"]
    item = (await boss.put("/api/store/job-cache/items/a", json={"value": {"n": 1}})).json()
    assert item["expires_at"] is not None
    await boss.put("/api/store/job-cache/items/a", json={"value": {"n": 2}})
    assert owner_conn.execute("select count(*) from store_revisions where container_id = %s", (cid,)).fetchone()[0] == 0
    assert (await boss.get("/api/store/job-cache/items/a/revisions")).json()["items"] == []
    owner_conn.execute("update store_items set expires_at = now() - interval '1 second' where container_id = %s", (cid,))
    assert (await boss.get("/api/store/job-cache/items/a")).status_code == 404
    async with db.connection() as conn:
        await jobs.enqueue(conn, "store.maintenance", {})
    await jobs.run_pending()
    assert owner_conn.execute("select count(*) from store_items where container_id = %s", (cid,)).fetchone()[0] == 0
    # A schema belongs to collections only; expiry to namespaces only.
    r = await boss.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "x", "name": "X", "json_schema": {}})
    assert r.status_code == 400


async def test_governance_rules(make_client, owner_conn):
    (boss, two, three), ids = await _admins(make_client, 3)
    gid = await _group(boss, ids[:2])
    # RACI: one A, at least one R; a non-admin can only be I.
    anna = make_client()
    await otp_login(anna, "55555555")
    anna_id = (await anna.get("/api/auth/me")).json()["user"]["id"]
    bad = [{"user_id": ids[0], "roles": ["R"]}]
    r = await boss.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "a", "name": "A", "raci": bad})
    assert r.status_code == 400 and "Accountable" in r.json()["detail"]
    bad = [{"user_id": ids[0], "roles": ["A", "R"]}, {"user_id": anna_id, "roles": ["C"]}]
    r = await boss.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "a", "name": "A", "raci": bad})
    assert r.status_code == 400 and "Datalager" in r.json()["detail"]
    # Only members of the group (or Fuld) create for it; defaults: creator A+R, rest of the group I.
    gid2 = await _group(boss, [ids[0]], "Andre")
    assert (await two.post("/api/admin/store", json={"kind": "kv", "group_id": gid2, "slug": "b", "name": "B"})).status_code == 403
    r = await two.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "b", "name": "B"})
    cid = r.json()["id"]
    detail = (await boss.get(f"/api/admin/store/{cid}")).json()
    assert sorted((p["id"], tuple(p["roles"])) for p in detail["raci"]) == [(ids[0], ("I",)), (ids[1], ("R", "A"))]
    assert detail["my_level"] == "manage"  # boss is super admin: Fuld adgang opens group containers
    assert (await two.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "b", "name": "B2"})).status_code == 409
    # Someone outside the group sees the metadata in /admin but not the items.
    assert (await three.get(f"/api/admin/store/{cid}")).json()["my_level"] is None
    assert (await three.get(f"/api/store/{cid}/items")).status_code == 404
    # Shares count only when shared; a share to a group lets its members in.
    gid3 = await _group(boss, [ids[2]], "Support")
    assert (await two.put(f"/api/admin/store/{cid}/shares", json={"shares": [{"group_id": gid3, "level": "write"}]})).status_code == 200
    assert (await three.get(f"/api/store/{cid}/items")).status_code == 404
    assert (await two.patch(f"/api/admin/store/{cid}", json={"visibility": "shared"})).status_code == 200
    assert (await three.put(f"/api/store/{cid}/items/k", json={"value": True})).status_code == 200
    # The A changes the RACI; archiving cancels a pending release and stops writes.
    assert (await three.put(f"/api/admin/store/{cid}/raci", json={"raci": [{"user_id": ids[2], "roles": ["A", "R"]}]})).status_code == 403
    assert (await two.put(f"/api/admin/store/{cid}/raci", json={"raci": [{"user_id": ids[1], "roles": ["A", "R"]},
                                                                          {"user_id": ids[2], "roles": ["R"]}]})).status_code == 200
    assert (await two.post(f"/api/admin/store/{cid}/archive", json={"reason": "Ikke brugt"})).status_code == 200
    assert (await three.put(f"/api/store/{cid}/items/k", json={"value": False})).status_code == 409
    assert _actions(owner_conn, cid) == ["create", "store.shares_changed", "update", "store.item_written", "store.raci_changed",
                                         "store.archived"]


async def test_gdpr_export_and_erasure(make_client, owner_conn):
    (boss, two), ids = await _admins(make_client, 2)
    gid = await _group(boss, ids)
    r = await boss.post("/api/admin/store", json={"kind": "kv", "group_id": gid, "slug": "delt", "name": "Delt",
                                                  "raci": [{"user_id": ids[1], "roles": ["A", "R"]}, {"user_id": ids[0], "roles": ["R"]}]})
    group_cid = r.json()["id"]
    assert (await two.post("/api/store", json={"kind": "nosql", "slug": "dagbog", "name": "Dagbog"})).status_code == 201
    await two.put("/api/store/~dagbog/items/d1", json={"value": {"tekst": "hej"}})
    async with db.connection() as conn:
        data = await gdpr.build_user_export(conn, ids[1])
    assert data["store_personal"][0]["items"][0]["value"] == {"tekst": "hej"}
    assert sorted((r["slug"], r["role"]) for r in data["store_roles"]) == [("delt", "A"), ("delt", "R")]
    await gdpr.erase_user(ids[1], actor_id=ids[0], meta=RequestMeta(), reason="test")
    assert owner_conn.execute("select count(*) from store_containers where owner_id is not null").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from store_raci where container_id = %s and role = 'A'", (group_cid,)).fetchone()[0] == 0
    assert "store.orphaned" in _actions(owner_conn, group_cid)
    items = (await boss.get("/api/admin/store")).json()["items"]
    assert [i["status"] for i in items if i["id"] == group_cid] == ["orphaned"]
