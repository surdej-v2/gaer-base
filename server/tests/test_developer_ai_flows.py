"""AI in Gær Developer, fase 5 (specs/developer/AI.md): Action-studie — a draft flow from a description (AI when its
flow validates, else a skeleton from keywords) and the risk check (rules): an approval nobody reads (with a fix),
a single approver, personal data sent on, a key in an HTTP step, mail inside For hver."""

from app.devkit import flow_assist
from tests.test_developer_ai import ai_on, reply  # noqa: F401  (ai_on is a fixture)
from tests.test_developer_flows import _call, _developer
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def test_a_skeleton_from_keywords_without_ai(make_client, owner_conn):
    dev, auth = await _developer(make_client, owner_conn)
    r = await _call(dev, auth, "post", f"{DEV}/flows/draft",
                    {"description": "Hver hverdag kl. 8 skal salgschefen godkende rapporten, og så sendes en mail til salg"})
    assert r.status_code == 200, r.text
    out = r.json()
    d = out["definition"]
    assert out["ai"] is False
    assert d["trigger"] == {"type": "schedule", "cron": "0 8 * * 1-5", "tz": "Europe/Copenhagen", "inputs": {}}
    assert [s["action"] for s in d["steps"]] == ["approval", "if"]
    assert d["steps"][1]["when"] == "{{ steps.godkend.approved }}"
    assert [s["action"] for s in d["steps"][1]["then"]] == ["file", "mail"]       # "rapporten" comes before "mail"
    assert any("klammer" in n for n in out["notes"])
    # The approval's answer is read, so no "not used"; but there is a single approver.
    assert [r["key"] for r in out["risks"]] == ["single_approver:godkend"]

    r = await _call(dev, auth, "post", f"{DEV}/flows/draft", {"description": "Når en ny bruger bliver oprettet, send en besked"})
    d = r.json()["definition"]
    assert d["trigger"]["type"] == "event" and d["trigger"]["events"] == ["user.created"]
    assert [s["action"] for s in d["steps"]] == ["message"]


def test_cron_from_words():
    assert flow_assist._cron("hver time") == "0 * * * *"
    assert flow_assist._cron("dagligt kl. 6.30") == "30 6 * * *"
    assert flow_assist._cron("hver mandag") == "0 7 * * 1"
    assert flow_assist._cron("når nogen trykker") is None


async def test_the_ai_draft_is_kept_only_when_it_validates(make_client, owner_conn, ai_on):
    dev, auth = await _developer(make_client, owner_conn)
    reply["text"] = ('{"trigger": {"type": "manual"}, "steps": [{"id": "hej", "action": "mail", "name": "Hej", '
                     '"with": {"to": ["{{ trigger.input.email }}"], "subject": "Velkommen", "text": "Hej"}}]}')
    out = (await _call(dev, auth, "post", f"{DEV}/flows/draft", {"description": "Send en velkomstmail"})).json()
    assert out["ai"] is True and out["definition"]["steps"][0]["with"]["subject"] == "Velkommen"
    reply["text"] = '{"trigger": {"type": "manual"}, "steps": [{"id": "x", "action": "findes_ikke"}]}'
    out = (await _call(dev, auth, "post", f"{DEV}/flows/draft", {"description": "Send en velkomstmail"})).json()
    assert out["ai"] is False and out["definition"]["steps"][0]["action"] == "mail"


async def test_risks_and_the_fix_for_an_unused_approval(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    r = await admin.post(f"{DEV}/operations", json={"slug": "lejere", "name": "Lejere", "definition": {
        "source": {"dataset": ids["lejemaal"]}, "fields": ["id", "ejendom_id", "lejer_navn"], "params": [], "limit": 50}})
    assert r.status_code == 201, r.text
    definition = {"trigger": {"type": "manual"}, "steps": [
        {"id": "ok", "action": "approval", "with": {"approvers": ["chef@example.dk"], "title": "Send?", "timeout_hours": 400}},
        {"id": "hent", "action": "operation", "with": {"operation": "lejere"}},
        {"id": "kald", "action": "http", "with": {"method": "POST", "url": "https://api.example.dk/x?token=abc123",
                                                  "headers": {"Authorization": "Bearer hemmelig"}}},
        {"id": "hver", "action": "foreach", "items": "{{ steps.hent.rows }}", "steps": [
            {"id": "mail", "action": "mail", "with": {"to": ["chef@example.dk"], "subject": "Lejer", "text": "{{ item.lejer_navn }}"}}]},
    ]}
    r = await admin.post(f"{DEV}/flows/risks", json={"definition": definition})
    assert r.status_code == 200, r.text
    risks = {x["key"]: x for x in r.json()["risks"]}
    assert set(risks) == {"approval_unused:ok", "single_approver:ok", "long_timeout:ok", "secret:kald", "bulk:mail", "personal:mail:hent"}
    assert "lejer_navn" in risks["personal:mail:hent"]["text"]
    assert [x["level"] for x in r.json()["risks"]][:3] == ["warn", "warn", "warn"]

    fixed = risks["approval_unused:ok"]["fix"]
    assert [s["id"] for s in fixed["steps"]] == ["ok", "ok_svar"]
    assert fixed["steps"][1]["when"] == "{{ steps.ok.approved }}" and [s["id"] for s in fixed["steps"][1]["then"]] == ["hent", "kald", "hver"]
    after = {x["key"] for x in (await admin.post(f"{DEV}/flows/risks", json={"definition": fixed})).json()["risks"]}
    assert "approval_unused:ok" not in after and "secret:kald" in after
    # A bad definition is the validator's 422.
    assert (await admin.post(f"{DEV}/flows/risks", json={"definition": {"trigger": {"type": "x"}}})).status_code == 422
