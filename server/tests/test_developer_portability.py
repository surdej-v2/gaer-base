"""Gær Developer, fase 6: export and import between instances (F-22 … F-24) — instance keys, a signed file with
references instead of ids and secrets, a check that blocks unknown senders and missing references, and an import that
creates versions through the approval flow."""

import copy

from tests.test_developer_api import _api, _call
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def test_keys_export_check_and_import(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, soeg, hent, _ = await _api(admin, ids)
    wk = (await admin.get("/.well-known/gaer-instance-keys")).json()["keys"]
    assert {k["use"] for k in wk} == {"sign", "encrypt"} and all(":" in k["fingerprint"] for k in wk)
    assert owner_conn.execute("select count(*) from dev_instance_keys").fetchone()[0] == 2
    assert b"BEGIN" not in bytes(owner_conn.execute("select private_enc from dev_instance_keys limit 1").fetchone()[0])

    doc = (await admin.post(f"{DEV}/export", json={"artifact_ids": [api_id, soeg, hent]})).json()
    assert doc["format"] == "gaer-export/1" and doc["secrets"] == "references" and doc["signature"]
    assert [a["type"] for a in doc["artifacts"]] == ["operation", "operation", "api"]
    op = doc["artifacts"][0]["definition"]
    assert op["source"] == {"dataset": {"$dataset": "gaer/lejemaal"}} and not any(k.startswith("_") for k in op)
    assert doc["artifacts"][2]["definition"]["operations"][0]["operation"]["$ref"].startswith("operation/")
    assert doc["catalog"] and doc["catalog"][0]["dataset"] == {"$dataset": "gaer/lejemaal"}
    assert (await admin.post(f"{DEV}/export", json={"artifact_ids": [api_id], "secret_values": True})).status_code == 422

    # Signed by this instance: the check passes and shows what would change.
    check = (await admin.post(f"{DEV}/import/check", json={"file": doc})).json()
    assert check["ok"] and check["signer"] == "denne instans"
    assert {a["action"] for a in check["actions"]} == {"update"}
    # A changed file breaks the signature; an unknown sender and a missing reference block the import.
    tampered = copy.deepcopy(doc)
    tampered["artifacts"][0]["definition"]["limit"] = 999
    assert "Signaturen" in " ".join((await admin.post(f"{DEV}/import/check", json={"file": tampered})).json()["problems"])
    missing = copy.deepcopy(doc)
    missing["artifacts"] = [a for a in missing["artifacts"] if a["type"] == "api"]
    missing["artifacts"][0]["definition"]["operations"][0]["operation"] = {"$ref": "operation/findes_ikke"}
    r = (await admin.post(f"{DEV}/import/check", json={"file": missing})).json()
    assert not r["ok"] and any("operation/findes_ikke" in p for p in r["problems"])
    r = await _call(admin, auth, "post", f"{DEV}/import", {"file": missing})
    assert r.status_code == 409 and "spærret" in r.json()["detail"]

    # Importing into a "new" instance: remove the artifacts here, then import the file.
    owner_conn.execute("delete from dev_artifacts where type in ('api', 'operation')")
    r = await _call(admin, auth, "post", f"{DEV}/import", {"file": doc})
    assert r.status_code == 200, r.text
    imported = r.json()["imported"]
    assert [x["type"] for x in imported][:3] == ["operation", "operation", "api"]
    key = await _call(admin, auth, "post", f"{DEV}/api-keys", {"name": "ERP", "scopes": ["GET *"]})
    res = await admin.get("/gaer/v1/lejeapi/lejemaal/2", headers={"Authorization": f"Bearer {key.json()['secret']}"})
    assert res.status_code == 200 and res.json()["data"] == [{"id": 2, "ejendom_id": "E-1"}]
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.import'").fetchone()[0] == 1
    assert owner_conn.execute("select count(*) from dev_imports").fetchone()[0] >= 4


async def test_known_instances_need_matching_fingerprint(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    keys = (await admin.get(f"{DEV}/instance-keys")).json()["keys"]
    sign = next(k for k in keys if k["use"] == "sign")
    r = await _call(admin, auth, "post", f"{DEV}/instances", {"name": "Anden instans", "sign_key": sign["public_key"], "fingerprint": "0000:1111:2222"})
    assert r.status_code == 422
    r = await _call(admin, auth, "post", f"{DEV}/instances", {"name": "Anden instans", "sign_key": sign["public_key"], "fingerprint": sign["fingerprint"]})
    assert r.status_code == 201
    assert (await admin.get(f"{DEV}/instances")).json()["items"][0]["name"] == "Anden instans"
