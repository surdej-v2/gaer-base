"""Gær Developer, fase 1: contracts and semver (F-2), masking (F-10), roles (DS-2, afsnit 10), artifacts with RACI,
approval and review (F-1, F-3 … F-6), the catalog as versioned artifacts (F-5, F-7, F-8) and GDPR."""

from datetime import date, datetime

import pytest

from app import admin_roles
from app.devkit import contracts, masking
from tests.helpers import full_user, otp_login, stepup

DEV = "/api/admin/dev"


# ── F-2: contracts ──────────────────────────────────────────────────────────

BASE = {"fields": {"adresse": {"type": "text", "classification": "green"},
                   "aarlig_leje": {"type": "number", "classification": "yellow"},
                   "lejer_navn": {"type": "text", "classification": "red", "personal": True}},
        "params": {"postnr": {"type": "string", "required": False}}, "items": ["soeg_lejemaal"], "key": "id"}


def _with(**kw):
    import copy
    c = copy.deepcopy(BASE)
    for k, v in kw.items():
        c[k] = v
    return c


def test_contract_patch_minor_major():
    assert contracts.diff(BASE, BASE, other_changes=["Beskrivelse ændret"])["type"] == "patch"
    assert contracts.diff(BASE, BASE)["type"] == "patch"
    added = _with(fields={**BASE["fields"], "areal_m2": {"type": "number", "classification": "green"}})
    assert contracts.diff(BASE, added)["type"] == "minor"
    assert contracts.diff(BASE, _with(items=["soeg_lejemaal", "hent_lejemaal"]))["type"] == "minor"
    assert contracts.diff(BASE, _with(params={**BASE["params"], "by": {"type": "string"}}))["type"] == "minor"
    removed = _with(fields={k: v for k, v in BASE["fields"].items() if k != "adresse"})
    assert contracts.diff(BASE, removed)["type"] == "major"
    retyped = _with(fields={**BASE["fields"], "adresse": {"type": "number", "classification": "green"}})
    assert contracts.diff(BASE, retyped)["type"] == "major"
    required = _with(params={**BASE["params"], "by": {"type": "string", "required": True}})
    assert contracts.diff(BASE, required)["type"] == "major"
    assert contracts.diff(BASE, _with(params={"postnr": {"type": "string", "required": True}}))["type"] == "major"
    assert contracts.diff(BASE, _with(items=[]))["type"] == "major"
    assert contracts.diff(BASE, _with(key="lejemaal_id"))["type"] == "major"


def test_downclassification_is_major_and_named():
    down = _with(fields={**BASE["fields"], "aarlig_leje": {"type": "number", "classification": "green"}})
    d = contracts.diff(BASE, down)
    assert d["type"] == "major" and d["downclassified"] == ["aarlig_leje"]
    unmarked = _with(fields={**BASE["fields"], "lejer_navn": {"type": "text", "classification": "red", "personal": False}})
    assert contracts.diff(BASE, unmarked)["downclassified"] == ["lejer_navn"]
    up = _with(fields={**BASE["fields"], "adresse": {"type": "text", "classification": "yellow"}})
    assert contracts.diff(BASE, up)["type"] == "minor"


def test_semver_bump():
    v = contracts.Version.parse("1.2.3")
    assert str(v.bump("patch")) == "1.2.4" and str(v.bump("minor")) == "1.3.0" and str(v.bump("major")) == "2.0.0"
    with pytest.raises(ValueError):
        contracts.Version.parse("1.2")


# ── F-10: masking ───────────────────────────────────────────────────────────

FIELDS = [{"name": "id", "classification": "white"}, {"name": "adresse", "classification": "green"},
          {"name": "aarlig_leje", "classification": "yellow"}, {"name": "lejer_navn", "classification": "red"},
          {"name": "email", "classification": "green", "personal_data": True}]


def test_masking_defaults_per_classification():
    policy = masking.policy_for(FIELDS)
    assert policy == {"id": "show", "adresse": "show", "aarlig_leje": "interval", "lejer_navn": "mask", "email": "mask"}
    row = {"id": 7, "adresse": "Eksempelvej 1", "aarlig_leje": 123456, "lejer_navn": "Jens Hansen", "email": "j@x.dk"}
    out = masking.mask_row(row, policy)
    assert out == {"id": 7, "adresse": "Eksempelvej 1", "aarlig_leje": "100000-150000", "lejer_navn": "•••", "email": "•••"}


def test_masking_refuses_weaker_policies():
    with pytest.raises(masking.MaskingError):
        masking.policy_for(FIELDS, {"lejer_navn": "show"})
    with pytest.raises(masking.MaskingError):
        masking.policy_for(FIELDS, {"aarlig_leje": "show"})
    with pytest.raises(masking.MaskingError):
        masking.policy_for(FIELDS, {"email": "round"})          # personal data counts as red
    with pytest.raises(masking.MaskingError):
        masking.policy_for(FIELDS, {"id": "secret"})
    # Stricter than the default is always allowed; the catalog's own choice is used without an override.
    assert masking.policy_for(FIELDS, {"adresse": "remove", "lejer_navn": "hash"})["lejer_navn"] == "hash"
    assert masking.policy_for([{"name": "x", "classification": "yellow", "mask": "round"}]) == {"x": "round"}
    assert "show" not in masking.allowed_for({"classification": "red"})


def test_masking_strategies():
    assert masking.mask_value(1234, "round") == 1200
    assert masking.mask_value(7, "round") == 7
    assert masking.mask_value(date(2026, 3, 9), "round") == "2026-03"
    assert masking.mask_value(datetime(2026, 3, 9, 10), "interval") == "2026-03"
    assert masking.mask_value(42, "interval") == "40-45"
    assert masking.mask_value(123456, "interval", step=1000) == "123000-124000"
    assert masking.mask_value("abc", "interval") == masking.MASK
    assert masking.mask_value("Jens Hansen", "partial") == "J••• H•••"
    assert masking.mask_value("jens@firma.dk", "partial") == "j•••@firma.dk"
    assert masking.mask_value("+45 12345678", "partial").endswith("78")
    h1, h2 = masking.mask_value("Jens", "hash"), masking.mask_value("Jens", "hash")
    assert h1 == h2 and h1.startswith("h:") and "Jens" not in h1
    assert masking.mask_value(None, "mask") is None
    assert masking.mask_value("x", "mask") == masking.MASK


def test_masking_rows_remove_aggregate_and_unknown_columns():
    policy = {"a": "show", "b": "remove", "c": "aggregate"}
    rows = masking.mask_rows([{"a": 1, "b": 2, "c": 3, "d": "hemmelig"}], policy)
    assert rows == [{"a": 1, "d": masking.MASK}]                # unknown column → masked (strict)
    assert masking.mask_rows([{"d": 1}], {}, strict=False) == [{"d": 1}]
    assert masking.mask_args({"postnr": "8000", "navn": "Jens"}, {"navn"}) == {"postnr": "8000", "navn": masking.MASK}
    assert masking.strictest(["white", "yellow", "green"]) == "yellow" and masking.strictest([]) == "white"


# ── Roles ───────────────────────────────────────────────────────────────────

def _uid(owner_conn, phone: str) -> int:
    return owner_conn.execute("select id from users where phone = %s", (phone,)).fetchone()[0]


async def _admin(make_client):
    admin = make_client()
    auth = await full_user(admin, "10000001")
    return admin, auth


async def _developer(make_client, owner_conn, phone: str, roles: list[str]):
    c = make_client()
    a = await full_user(c, phone)
    owner_conn.execute("update users set role = 'admin', admin_roles = %s, audience = 'internal' where phone = %s",
                       (roles, f"+45{phone}"))
    return c, a


async def _call(client, auth, method, url, body=None):
    send = getattr(client, method)
    r = await (send(url, json=body) if body is not None else send(url))
    if r.status_code == 403 and r.headers.get("x-gaer-error") in ("stepup_required", "mfa_required"):
        await stepup(client, auth)
        r = await (send(url, json=body) if body is not None else send(url))
    return r


def test_roles_in_the_admin_model():
    assert "/developer" in admin_roles.sections(["udvikler"]) and "/developer" in admin_roles.sections(["udvikler_admin"])
    assert admin_roles.allows(["udvikler"], "GET", f"{DEV}/overview")
    assert admin_roles.allows(["udvikler"], "GET", f"{DEV}/connections")
    assert not admin_roles.allows(["udvikler"], "POST", f"{DEV}/connections")
    assert not admin_roles.allows(["udvikler"], "PUT", f"{DEV}/developers/3")
    assert admin_roles.allows(["udvikler_admin"], "POST", f"{DEV}/connections")
    assert not admin_roles.allows(["udvikler"], "GET", "/api/admin/users")
    assert admin_roles.is_developer(["data"]) and not admin_roles.is_developer(["support"])


async def test_developer_roles_and_external_users(make_client, owner_conn):
    admin, admin_auth = await _admin(make_client)
    dev, dev_auth = await _developer(make_client, owner_conn, "10000002", ["udvikler_admin"])
    plain = make_client()
    await otp_login(plain, "10000003")
    plain_id = _uid(owner_conn, "+4510000003")
    assert (await dev.get(f"{DEV}/overview")).status_code == 200
    assert (await plain.get(f"{DEV}/overview")).status_code == 403

    # External users can never get the roles: the endpoint refuses, and so does the database.
    r = await _call(dev, dev_auth, "put", f"{DEV}/developers/{plain_id}", {"role": "udvikler"})
    assert r.status_code == 422 and "Eksterne" in r.json()["detail"]
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        owner_conn.execute("update users set role = 'admin', admin_roles = '{udvikler}' where id = %s", (plain_id,))

    owner_conn.execute("update users set audience = 'internal' where id = %s", (plain_id,))
    r = await _call(dev, dev_auth, "put", f"{DEV}/developers/{plain_id}", {"role": "udvikler"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "role": "admin", "adminRoles": ["udvikler"]}
    # Udvikler-admin cannot hand out Udvikler-admin; Fuld can.
    r = await _call(dev, dev_auth, "put", f"{DEV}/developers/{plain_id}", {"role": "udvikler_admin"})
    assert r.status_code == 403
    r = await _call(admin, admin_auth, "put", f"{DEV}/developers/{plain_id}", {"role": "udvikler_admin"})
    assert r.status_code == 200 and r.json()["adminRoles"] == ["udvikler_admin"]
    r = await _call(admin, admin_auth, "put", f"{DEV}/developers/{plain_id}", {"role": "none"})
    assert r.json()["role"] == "user"
    assert owner_conn.execute("select count(*) from audit_log where action = 'role.changed' and row_id = %s",
                              (str(plain_id),)).fetchone()[0] == 3
    # A plain Udvikler may not give roles at all.
    udv, udv_auth = await _developer(make_client, owner_conn, "10000004", ["udvikler"])
    r = await _call(udv, udv_auth, "put", f"{DEV}/developers/{plain_id}", {"role": "udvikler"})
    assert r.status_code == 403


# ── Artifacts through the catalog ───────────────────────────────────────────

async def _dataset(admin, owner_conn):
    r = await admin.post("/api/admin/data/datasets", json={"table": "clips"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_catalog_change_is_a_version_and_patch_needs_no_approval(make_client, owner_conn):
    admin, admin_auth = await _admin(make_client)
    ds = await _dataset(admin, owner_conn)
    r = await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"description": "Brugernes klip",
                                                              "fields": [{"name": "title", "description": "Titlen"}]})
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    assert v["status"] == "published" and v["version"] == "1.0.1" and v["changeType"] == "patch"
    detail = (await admin.get(f"{DEV}/catalog/datasets/{ds}")).json()
    assert detail["dataset"]["description"] == "Brugernes klip"
    assert next(f for f in detail["fields"] if f["name"] == "title")["description"] == "Titlen"
    assert [x["version"] for x in detail["artifact"]["versions"]] == ["1.0.1", "1.0.0"]
    # The creator is A and R.
    assert detail["artifact"]["raci"][0]["roles"] == ["R", "A"]
    actions = [r[0] for r in owner_conn.execute("select action from audit_log where table_name = 'dev_artifacts' order by id")]
    assert "dev.artifact.created" in actions and actions.count("dev.version.published") == 2


async def test_approval_flow_minor_needs_r_and_c_major_needs_reason_and_a(make_client, owner_conn):
    admin, admin_auth = await _admin(make_client)
    owner_conn.execute("update users set audience = 'internal'")
    c_user, c_auth = await _developer(make_client, owner_conn, "10000005", ["udvikler"])
    i_user, _ = await _developer(make_client, owner_conn, "10000006", ["udvikler"])
    admin_id, c_id, i_id = (_uid(owner_conn, p) for p in ("+4510000001", "+4510000005", "+4510000006"))
    ds = await _dataset(admin, owner_conn)
    first = await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"description": "Klip"})
    art = first.json()["artifactId"]
    r = await admin.put(f"{DEV}/artifacts/{art}/raci", json={"assignments": [
        {"userId": admin_id, "roles": ["A", "R"]}, {"userId": c_id, "roles": ["C"]}, {"userId": i_id, "roles": ["I"]}]})
    assert r.status_code == 200, r.text
    r = await admin.patch(f"{DEV}/artifacts/{art}", json={"approval_flow": True})
    assert r.json()["approvalFlow"] is True

    # Minor (stricter classification): R (the author, counted at once) and C must approve.
    r = await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"fields": [{"name": "title", "classification": "red"}]})
    v = r.json()["version"]
    assert v["status"] == "pending" and v["changeType"] == "minor" and v["version"] == "1.1.0"
    mine = (await c_user.get(f"{DEV}/approvals?scope=mine")).json()["items"]
    assert [x["id"] for x in mine] == [v["id"]] and mine[0]["role"] == "C"
    detail = (await c_user.get(f"{DEV}/approvals/{v['id']}")).json()
    assert detail["mine"] and detail["needs"] == ["R", "C"] and detail["from"] == "1.0.1"
    assert {(a["role"], a["decision"]) for a in detail["approvals"]} == {("R", "pending"), ("C", "pending")} or \
        {(a["role"], a["decision"]) for a in detail["approvals"]} == {("R", "approved"), ("C", "pending")}
    # The catalog is unchanged until it is published.
    assert owner_conn.execute("select classification from dataset_fields where dataset_id = %s and name = 'title'",
                              (ds,)).fetchone()[0] == "yellow"
    assert (await admin.post(f"{DEV}/approvals/{v['id']}", json={"approve": True})).status_code == 403   # not pending for A
    r = await c_user.post(f"{DEV}/approvals/{v['id']}", json={"approve": True, "note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "published"
    assert owner_conn.execute("select classification from dataset_fields where dataset_id = %s and name = 'title'",
                              (ds,)).fetchone()[0] == "red"
    # Informed got a message (in the app, and a mail job).
    notes = (await i_user.get(f"{DEV}/notifications")).json()["items"]
    assert any(n["kind"] == "published" for n in notes)

    # Major: downclassification needs a reason, then A, R and C.
    r = await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"fields": [{"name": "title", "classification": "green"}]})
    assert r.status_code == 422 and "begrundelse" in r.json()["detail"]
    r = await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"fields": [{"name": "title", "classification": "green"}],
                                                              "reason": "Titler er ikke følsomme"})
    v = r.json()["version"]
    assert v["changeType"] == "major" and v["version"] == "2.0.0" and v["status"] == "pending"
    r = await c_user.post(f"{DEV}/approvals/{v['id']}", json={"approve": False})
    assert r.status_code == 422
    r = await c_user.post(f"{DEV}/approvals/{v['id']}", json={"approve": False, "note": "Nej"})
    assert r.json()["status"] == "rejected"
    assert owner_conn.execute("select classification from dataset_fields where dataset_id = %s and name = 'title'",
                              (ds,)).fetchone()[0] == "red"
    history = (await admin.get(f"{DEV}/approvals?scope=history")).json()["items"]
    assert {h["status"] for h in history} >= {"rejected", "published"}


async def test_major_flags_dependents_for_review(make_client, owner_conn):
    admin, admin_auth = await _admin(make_client)
    owner_conn.execute("update users set audience = 'internal'")
    ds = await _dataset(admin, owner_conn)
    art = (await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"description": "Klip"})).json()["artifactId"]
    ds2 = (await admin.post("/api/admin/data/datasets", json={"table": "files"})).json()["id"]
    user_art = (await admin.put(f"{DEV}/catalog/datasets/{ds2}", json={"description": "Filer"})).json()["artifactId"]
    # files' published version uses clips (stands in for an operation or pipeline that reads it).
    vid = owner_conn.execute("select current_version_id from dev_artifacts where id = %s", (user_art,)).fetchone()[0]
    owner_conn.execute("insert into dev_artifact_dependencies (version_id, depends_on, detail) values (%s, %s, 'title')",
                       (vid, art))
    detail = (await admin.get(f"{DEV}/artifacts/{art}")).json()
    assert [u["id"] for u in detail["usedBy"]] == [user_art]

    r = await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"fields": [{"name": "title", "classification": "green"}],
                                                              "reason": "Ikke følsomt"})
    assert r.json()["version"]["status"] == "published"      # no approval flow: published, but still a major
    flagged = (await admin.get(f"{DEV}/artifacts/{user_art}")).json()
    assert flagged["status"] == "review_required" and flagged["reviews"][0]["status"] == "open"
    overview = (await admin.get(f"{DEV}/overview")).json()
    assert overview["counts"]["reviews"] == 1
    r = await admin.post(f"{DEV}/reviews/{flagged['reviews'][0]['id']}/approve", json={"note": "Tjekket"})
    assert r.status_code == 200 and r.json() == {"open": 0}
    assert (await admin.get(f"{DEV}/artifacts/{user_art}")).json()["status"] == "active"


async def test_raci_rules_and_search_and_gdpr(make_client, owner_conn):
    admin, admin_auth = await _admin(make_client)
    owner_conn.execute("update users set audience = 'internal'")
    admin_id = _uid(owner_conn, "+4510000001")
    ds = await _dataset(admin, owner_conn)
    art = (await admin.put(f"{DEV}/catalog/datasets/{ds}", json={"description": "Klip"})).json()["artifactId"]
    r = await admin.put(f"{DEV}/artifacts/{art}/raci", json={"assignments": [{"userId": admin_id, "roles": ["R"]}]})
    assert r.status_code == 422 and "Accountable" in r.json()["detail"]
    r = await admin.put(f"{DEV}/artifacts/{art}/raci", json={"assignments": [{"userId": admin_id, "roles": ["A"]}]})
    assert r.status_code == 422 and "Responsible" in r.json()["detail"]
    items = (await admin.get(f"{DEV}/search?q=kli")).json()["items"]
    assert {i["kind"] for i in items} == {"artifact", "dataset"}
    r = await _call(admin, admin_auth, "get", "/api/me/export")
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
        assert "developer_roles" in r.json()


async def test_suggestions_without_ai(make_client, owner_conn):
    admin, admin_auth = await _admin(make_client)
    owner_conn.execute("update users set audience = 'internal'")
    ds = await _dataset(admin, owner_conn)
    other = (await admin.post("/api/admin/data/datasets", json={"table": "files"})).json()["id"]
    owner_conn.execute("update dataset_fields set classification = 'red', personal_data = true where dataset_id = %s and name = 'user_id'",
                       (other,))
    r = await admin.post(f"{DEV}/catalog/datasets/{ds}/suggest")
    assert r.status_code == 200 and r.json()["ai"] is False
    s = {x["field"]: x for x in r.json()["suggestions"]}
    assert s["user_id"]["classification"] == "red" and s["user_id"]["personal_data"] is True
