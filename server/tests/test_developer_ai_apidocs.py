"""AI in Gær Developer, fase 4 (specs/developer/AI.md): the API documentation's texts — drafts from rules when a
version is published (job developer.docs), rewritten by AI when it is on, approved by an owner and only then published
in the OpenAPI document; a guide for the new major from the contract diff; errors from dev_api_log counts only."""

from app import jobs
from tests.test_developer_ai import ai_on, reply, sent  # noqa: F401  (ai_on is a fixture)
from tests.test_developer_api import _api, _key
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def _docs_job(owner_conn):
    for (payload,) in owner_conn.execute("select payload from jobs where kind = 'developer.docs' and status = 'queued' order by id").fetchall():
        await jobs.HANDLERS["developer.docs"](payload, {})


async def test_rule_drafts_approval_and_publishing(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, _, _, definition = await _api(admin, ids)
    await _docs_job(owner_conn)
    docs = (await admin.get(f"{DEV}/apis/{api_id}/docs")).json()
    assert docs["major"] == 1 and docs["canApprove"] is True and docs["ai"] is False
    by = {s["section"]: s for s in docs["sections"]}
    assert list(by) == ["intro", "operation:GET /lejemaal", "operation:GET /lejemaal/:id", "operation:POST /lejemaal/soeg", "errors"]
    intro = by["intro"]["draft"]
    assert "/gaer/v1/lejeapi" in intro and "lejer_navn" in intro and by["intro"]["source"] == "rule"
    assert "Parametre: id (påkrævet)." in by["operation:GET /lejemaal/:id"]["draft"]
    assert "Idempotency-Key" in by["operation:POST /lejemaal/soeg"]["draft"]
    assert docs["pending"] == 5 and by["intro"]["approved"] is None
    curl = docs["examples"]["GET /lejemaal"]["curl"]
    assert curl.startswith('curl "') and "/gaer/v1/lejeapi/lejemaal?ejendom=E-1" in curl and "$GAER_API_KEY" in curl

    # Nothing is published before an owner approves.
    spec = (await admin.get("/gaer/v1/lejeapi/openapi.json")).json()
    assert spec["info"]["description"] == "" and "description" not in spec["paths"]["/lejemaal"]["get"]
    r = await admin.post(f"{DEV}/apis/{api_id}/docs/edit", json={"major": 1, "section": "operation:GET /lejemaal",
                                                                "text": "Søg lejemål på en ejendom."})
    assert r.status_code == 200
    for section in ("intro", "operation:GET /lejemaal"):
        assert (await admin.post(f"{DEV}/apis/{api_id}/docs/approve", json={"major": 1, "section": section})).status_code == 200
    spec = (await admin.get("/gaer/v1/lejeapi/openapi.json")).json()
    assert spec["info"]["description"] == intro
    assert spec["paths"]["/lejemaal"]["get"]["description"] == "Søg lejemål på en ejendom."
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.api.docs.approved'").fetchone()[0] == 2
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.api.docs.edited'").fetchone()[0] == 1

    # A rejected draft goes; approving nothing, or as somebody who is not an owner, is refused.
    assert (await admin.post(f"{DEV}/apis/{api_id}/docs/reject", json={"major": 1, "section": "errors"})).status_code == 200
    assert (await admin.post(f"{DEV}/apis/{api_id}/docs/approve", json={"major": 1, "section": "errors"})).status_code == 409
    owner_conn.execute("delete from dev_artifact_roles where artifact_id = %s", (api_id,))
    r = await admin.post(f"{DEV}/apis/{api_id}/docs/approve", json={"major": 1, "section": "operation:GET /lejemaal/:id"})
    assert r.status_code == 403
    assert (await admin.post(f"{DEV}/apis/{api_id}/docs/whatever", json={"major": 1, "section": "intro"})).status_code == 404

    # The texts a person approved are in their GDPR export.
    from app import db, gdpr
    uid = owner_conn.execute("select id from users where phone = '+4520000001'").fetchone()[0]
    async with db.connection() as conn:
        export = await gdpr.build_user_export(conn, uid)
    assert {r["section"] for r in export["developer_api_docs_approved"]} == {"intro", "operation:GET /lejemaal"}


async def test_a_new_major_gets_a_guide_and_errors_from_counts(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, _, _, definition = await _api(admin, ids)
    key = await _key(admin, auth, ["GET *"], api_id=api_id)
    h = {"Authorization": f"Bearer {key['secret']}"}
    assert (await admin.get("/gaer/v1/lejeapi/lejemaal/1", headers=h)).status_code == 200
    for _ in range(3):
        assert (await admin.get("/gaer/v1/lejeapi/lejemaal/abc", headers=h)).status_code == 422   # logged: reached the operation
    v2 = {**definition, "operations": [o for o in definition["operations"] if o["path"] != "/lejemaal/soeg"]}
    assert (await admin.put(f"{DEV}/apis/{api_id}", json={"definition": v2, "submit": True})).json()["version"]["version"] == "2.0.0"
    await _docs_job(owner_conn)
    docs = (await admin.get(f"{DEV}/apis/{api_id}/docs")).json()
    by = {s["section"]: s for s in docs["sections"]}
    assert docs["major"] == 2 and by["migration"]["title"] == "Skift fra v1"
    guide = by["migration"]["draft"]
    assert "/gaer/v2/lejeapi" in guide and "POST /lejemaal/soeg" in guide and "svarer 410 fra" in guide
    assert "1 nøgle kaldte v1" in guide and docs["oldKeys"] == ["ERP"]
    errors = by["errors"]["draft"]
    assert "422 (100 % af fejlene de seneste 30 dage)" in errors and '{"error": "…"}' in errors
    assert key["keyId"] not in errors and "abc" not in errors and "lejemaal/" not in errors   # counts per status only
    assert docs["errorCounts"] == [{"status": 422, "count": 3, "pct": 100}]


async def test_ai_rewrites_but_adds_no_sections(make_client, owner_conn, ai_on):
    admin, auth, ids = await _setup(make_client, owner_conn)
    api_id, _, _, _ = await _api(admin, ids)
    reply["text"] = '{"intro": "Velkommen til Lejedata. Brug /gaer/v1/lejeapi med en nøgle.", "noget_nyt": "x"}'
    out = (await admin.post(f"{DEV}/apis/{api_id}/docs/generate")).json()
    assert out["ai"] is True and out["written"] == 5
    by = {s["section"]: s for s in (await admin.get(f"{DEV}/apis/{api_id}/docs")).json()["sections"]}
    assert by["intro"]["draft"] == "Velkommen til Lejedata. Brug /gaer/v1/lejeapi med en nøgle." and by["intro"]["source"] == "ai"
    assert by["errors"]["source"] == "rule" and "noget_nyt" not in by
    assert "lejer_navn" in str(sent[-1]["messages"])           # the facts went in; no rows, no keys
    # A person's draft is not overwritten by a new generation.
    await admin.post(f"{DEV}/apis/{api_id}/docs/edit", json={"major": 1, "section": "intro", "text": "Min egen tekst."})
    await admin.post(f"{DEV}/apis/{api_id}/docs/generate")
    by = {s["section"]: s for s in (await admin.get(f"{DEV}/apis/{api_id}/docs")).json()["sections"]}
    assert by["intro"]["draft"] == "Min egen tekst." and by["intro"]["source"] == "human"
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.api.docs.generated'").fetchone()[0] == 2
