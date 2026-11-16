"""AI in Gær Developer, fase 3 (specs/developer/AI.md): the MCP test bench — coverage per tool, proposed test cases
(rules: untested tools, an empty answer for an unknown value, access as an external test account), the new checks
(max_rows, protected, only_tools), AI questions and variants, and why a test case fails."""

from app.devkit import mcp_studio
from tests.test_developer_ai import ai_on, reply, sent  # noqa: F401  (ai_on is a fixture)
from tests.test_developer_datastudie import _guide
from tests.test_developer_mcp_studio import _test_account
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def test_proposals_cover_untested_tools_edges_and_access(make_client, owner_conn):
    admin, _, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids)
    out = (await admin.get(f"{DEV}/mcp/lejedata/suggestions")).json()
    assert out["ai"] is False
    assert out["coverage"] == [{"tool": "soeg_lejemaal", "testcases": 0, "calls30d": 0, "passed": None}]
    by = {p["key"]: p for p in out["items"]}
    typical = by["untested:soeg_lejemaal"]["testcase"]
    assert typical == {"kind": "direct", "name": "soeg_lejemaal: et typisk kald", "tool": "soeg_lejemaal", "args": {},
                       "expect": {"min_rows": 1}}
    empty = by["empty:soeg_lejemaal:ejendom"]["testcase"]
    assert empty["args"] == {"ejendom": "findes-ikke-test"} and empty["expect"] == {"max_rows": 0}
    assert not any(k.startswith("protected:") for k in by)     # a white tool: nothing to protect

    # Saved as proposed, both pass; coverage and the proposals follow.
    for p in (typical, empty):
        assert (await admin.post(f"{DEV}/mcp/lejedata/testcases", json=p)).status_code == 201
    run = (await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": False})).json()
    assert run["passed"] is True, run
    out = (await admin.get(f"{DEV}/mcp/lejedata/suggestions")).json()
    assert out["coverage"][0]["testcases"] == 2 and out["coverage"][0]["passed"] is True
    assert out["items"] == []


async def test_a_yellow_tool_is_tested_as_an_external_account(make_client, owner_conn):
    admin, _, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids, level="yellow")
    out = (await admin.get(f"{DEV}/mcp/lejedata/suggestions")).json()
    assert any("ekstern testkonto" in n for n in out["notes"])
    uid = await _test_account(admin, owner_conn)
    out = (await admin.get(f"{DEV}/mcp/lejedata/suggestions")).json()
    prot = next(p["testcase"] for p in out["items"] if p["key"].startswith("protected:"))
    assert prot["as_user_id"] == uid and prot["expect"] == {"protected": True}
    assert (await admin.post(f"{DEV}/mcp/lejedata/testcases", json=prot)).status_code == 201
    run = (await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": False})).json()
    assert [r["passed"] for r in run["results"]] == [True]   # refused for an external account: protected


def test_judge_new_checks():
    direct = {"kind": "direct", "expect": {"max_rows": 0}}
    assert mcp_studio.judge(direct, {"isError": False, "result": {"count": 0, "rows": []}}) == (True, [])
    assert mcp_studio.judge(direct, {"isError": False, "result": {"count": 3, "rows": [{}]}})[0] is False
    prot = {"kind": "direct", "expect": {"protected": True}}
    assert mcp_studio.judge(prot, {"isError": True, "result": {"error": "Kun for interne"}})[0] is True
    assert mcp_studio.judge(prot, {"isError": False, "result": {"rows": [{"a": 1}]}, "masked": ["lejer_navn"]})[0] is True
    ok, problems = mcp_studio.judge(prot, {"isError": False, "result": {"rows": [{"lejer_navn": "Lejer 7"}]}, "masked": []})
    assert not ok and "klartekst" in problems[0]
    only = {"kind": "ai", "expect": {"tools": ["a"], "only_tools": True}}
    assert mcp_studio.judge(only, {"steps": [{"tool": "a", "args": {}}], "answer": ""})[0] is True
    assert mcp_studio.judge(only, {"steps": [{"tool": "a", "args": {}}, {"tool": "b", "args": {}}], "answer": ""}) == \
        (False, ["AI kaldte også b"])


async def test_explain_points_at_a_changed_description(make_client, owner_conn):
    admin, _, ids = await _setup(make_client, owner_conn)
    eid = await _guide(admin, ids)
    tc = (await admin.post(f"{DEV}/mcp/lejedata/testcases", json={"kind": "direct", "name": "Mange", "tool": "soeg_lejemaal",
                                                                   "args": {}, "expect": {"min_rows": 1000}})).json()["id"]
    assert (await admin.get(f"{DEV}/mcp/lejedata/testcases/{tc}/explain")).json()["text"] == "Testcasen er ikke kørt endnu."
    detail = (await admin.get(f"{DEV}/datastudie/endpoints/{eid}")).json()
    tools = [{**t, "description": "Slå lejemål op."} for t in detail["tools"]]
    r = await admin.put(f"{DEV}/datastudie/endpoints/{eid}", json={"definition": {**detail["definition"], "tools": tools}})
    assert r.status_code == 200, r.text
    await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": False})
    out = (await admin.get(f"{DEV}/mcp/lejedata/testcases/{tc}/explain")).json()
    assert out["passed"] is False and out["ai"] is False
    assert "forventede mindst 1000" in out["problems"][0]
    assert out["changes"] == [{"tool": "soeg_lejemaal", "before": "Søg lejemål på ejendom. Returnerer leje som interval.",
                               "after": "Slå lejemål op."}]
    assert "Beskrivelsen af soeg_lejemaal er ændret i kladden" in out["text"]
    assert out["endpointPath"] == f"/developer/datastudie/endpoints/{eid}"
    assert (await admin.get(f"{DEV}/mcp/lejedata/testcases/999999/explain")).status_code == 404


async def test_ai_questions_variants_and_explanation(make_client, owner_conn, ai_on):
    admin, _, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids)
    reply["text"] = '[{"tool": "soeg_lejemaal", "question": "Hvilke lejemål har ejendom E-1?"}, {"tool": "findes_ikke", "question": "x"}]'
    out = (await admin.get(f"{DEV}/mcp/lejedata/suggestions", params={"ai": "true"})).json()
    ai_items = [p for p in out["items"] if p["key"].startswith("ai:")]
    assert [p["testcase"] for p in ai_items] == [{"kind": "ai", "name": "Hvilke lejemål har ejendom E-1?",
                                                 "question": "Hvilke lejemål har ejendom E-1?", "expect": {"tools": ["soeg_lejemaal"]}}]
    reply["text"] = '["Lejemål på E-1?", "Hvad er lejet ud i E-1?", "Hvilke lejemål har ejendom E-1?"]'
    v = (await admin.post(f"{DEV}/mcp/lejedata/variants", json={"question": "Hvilke lejemål har ejendom E-1?"})).json()
    assert v["variants"] == ["Lejemål på E-1?", "Hvad er lejet ud i E-1?"]


async def test_variants_need_ai_and_the_ai_test_respects_the_budget(make_client, owner_conn, ai_on):
    admin, _, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids)
    uid = owner_conn.execute("select id from users where phone = '+4520000001'").fetchone()[0]
    owner_conn.execute("update users set ai_monthly_tokens = 10 where id = %s", (uid,))
    owner_conn.execute("""insert into ai_usage (user_id, surface, model, prompt_tokens, completion_tokens, estimated)
                          values (%s, 'developer', 'm', 40, 10, false)""", (uid,))
    sent.clear()
    r = await admin.post(f"{DEV}/mcp/lejedata/ai", json={"question": "Hvor mange lejemål har E-1?"})
    assert r.status_code == 429 and sent == []
