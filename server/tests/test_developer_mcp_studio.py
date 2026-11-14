"""Gær Developer, fase 4: MCP-studie — endpoints overview (MCP-1), test bench as a test account and against the draft
(MCP-9), AI test and saved test cases that gate a major (MCP-10), clients and revocation (MCP-12), monitoring, alarms
and rate limits (MCP-14, MCP-15)."""

import dataclasses
import json

import httpx
import pytest

from app import ai, config, jobs
from tests.helpers import otp_login
from tests.mcp_client import McpClient
from tests.mcp_helpers import mcp_token
from tests.test_developer_datastudie import _guide
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def _test_account(admin, owner_conn, audience="external"):
    r = await admin.post("/api/impersonation/users", json={"display_name": "Ekstern test", "role": "user"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    owner_conn.execute("update users set audience = %s where id = %s", (audience, uid))
    return uid


async def test_overview_and_bench_as_test_account_and_draft(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    eid = await _guide(admin, ids, level="yellow")
    items = {e["key"]: e for e in (await admin.get(f"{DEV}/mcp/endpoints")).json()["items"]}
    assert {"app", "admin", "lejedata"} <= set(items)
    assert items["lejedata"]["origin"] == "Datastudie" and items["lejedata"]["version"] == "1.0.0" and items["lejedata"]["owners"]
    bench = (await admin.get(f"{DEV}/mcp/lejedata/bench")).json()
    assert "soeg_lejemaal" in {t["name"] for t in bench["tools"]} and bench["ai"] is False
    # As the developer (an admin with an internal audience but no grant): the yellow tool needs access.
    r = (await admin.post(f"{DEV}/mcp/lejedata/call", json={"tool": "soeg_lejemaal", "args": {"ejendom": "E-1"}})).json()
    assert r["isError"] and "adgang" in r["result"]["error"]
    # As an external test account: refused as well, and only for internal users.
    uid = await _test_account(admin, owner_conn)
    r = (await admin.post(f"{DEV}/mcp/lejedata/call", json={"tool": "soeg_lejemaal", "args": {}, "as_user_id": uid})).json()
    assert r["isError"] and "interne" in r["result"]["error"]
    # Against the draft: the operation's draft runs, masked, with time and cache status.
    r = (await admin.post(f"{DEV}/mcp/lejedata/call", json={"tool": "soeg_lejemaal", "args": {"ejendom": "E-1"}, "draft": True})).json()
    assert not r["isError"] and r["draft"] and r["result"]["count"] == 12 and "lejer_navn" in r["masked"]
    # Somebody else's test account cannot be used.
    other = make_client()
    await otp_login(other, "20000020")
    assert (await admin.post(f"{DEV}/mcp/lejedata/call", json={"tool": "whoami", "args": {}, "as_user_id": 999999})).status_code == 404


async def test_testcases_gate_a_major(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    eid = await _guide(admin, ids)
    r = await admin.post(f"{DEV}/mcp/lejedata/testcases", json={"kind": "direct", "name": "E-1 har lejemål", "tool": "soeg_lejemaal",
                                                               "args": {"ejendom": "E-1"}, "expect": {"min_rows": 3}})
    assert r.status_code == 201
    run = (await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": False})).json()
    assert run["passed"] is True
    # Renaming the tool removes the old one: a major. The saved test case fails against the draft → blocked.
    detail = (await admin.get(f"{DEV}/datastudie/endpoints/{eid}")).json()
    tools = [{**t, "name": "find_lejemaal"} for t in detail["tools"]]
    r = await admin.put(f"{DEV}/datastudie/endpoints/{eid}", json={"definition": {**detail["definition"], "tools": tools}})
    assert r.json()["version"]["changeType"] == "major"
    r = await admin.post(f"{DEV}/artifacts/{eid}/submit", json={})
    assert r.status_code == 409 and "testcases" in r.json()["detail"]
    run = (await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": True})).json()
    assert run["passed"] is False
    # A test case for the new tool, run against the draft (the old one removed): now it may be submitted.
    tc = (await admin.get(f"{DEV}/mcp/lejedata/bench")).json()["testcases"][0]
    await admin.delete(f"{DEV}/mcp/lejedata/testcases/{tc['id']}")
    await admin.post(f"{DEV}/mcp/lejedata/testcases", json={"kind": "direct", "name": "Ny", "tool": "find_lejemaal",
                                                           "args": {"ejendom": "E-1"}, "expect": {"min_rows": 1, "contains": "E-1"}})
    run = (await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": True})).json()
    assert run["passed"] is True, run
    r = await admin.post(f"{DEV}/artifacts/{eid}/submit", json={})
    assert r.status_code == 200 and r.json()["status"] == "published"


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_api_key="test-key"))
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "soeg_lejemaal", "arguments": json.dumps({"ejendom": "E-1"})}}]}}]})
        tool_result = json.loads(next(m for m in body["messages"] if m["role"] == "tool")["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": f"Der er {tool_result['count']} lejemål på E-1."}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    monkeypatch.setattr(ai, "transport", httpx.MockTransport(handler))
    return sent


async def test_ai_test_and_ai_testcase(make_client, owner_conn, ai_on):
    admin, auth, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids)
    r = await admin.post(f"{DEV}/mcp/lejedata/ai", json={"question": "Hvor mange lejemål har E-1?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [s["tool"] for s in out["steps"]] == ["soeg_lejemaal"] and out["steps"][0]["args"] == {"ejendom": "E-1"}
    assert "lejemål" in out["answer"]
    # The model got the endpoint's tool definitions and instructions.
    first = next(b for b in ai_on if "tools" in b)
    assert any(t["function"]["name"] == "soeg_lejemaal" for t in first["tools"])
    assert first["messages"][0]["content"] == "Svar kort."
    await admin.post(f"{DEV}/mcp/lejedata/testcases", json={"kind": "ai", "name": "AI vælger søgning", "question": "Lejemål på E-1?",
                                                           "expect": {"tools": ["soeg_lejemaal"], "args": {"soeg_lejemaal": {"ejendom": "E-1"}}}})
    run = (await admin.post(f"{DEV}/mcp/lejedata/testcases/run", json={"draft": False})).json()
    assert run["passed"] is True, run


async def test_rate_limits_clients_monitoring_and_alarms(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids)
    r = await admin.put(f"{DEV}/mcp/lejedata/limits", json={"per_user": 2, "error_alarm_pct": 5})
    assert r.json()["limits"] == {"per_user": 2, "error_alarm_pct": 5}
    user = make_client()
    await otp_login(user, "20000030")
    owner_conn.execute("update users set audience = 'internal' where phone = '+4520000030'")
    token = (await mcp_token(user, "mcp"))["access_token"]
    m = await McpClient(user, "/mcp/lejedata", token).initialize()
    assert not (await m.call("soeg_lejemaal", ejendom="E-1"))[1]
    assert not (await m.call("soeg_lejemaal", ejendom="E-2"))[1]
    result, is_error = await m.call("soeg_lejemaal", ejendom="E-1")
    assert is_error and "For mange kald" in result["error"]

    clients = (await admin.get(f"{DEV}/mcp/lejedata/clients")).json()["clients"]
    assert clients[0]["calls_today"] >= 3 and clients[0]["sessions"] == 1
    r = await admin.post(f"{DEV}/mcp/lejedata/clients/revoke", json={"client_id": clients[0]["client_id"], "user_id": clients[0]["user_id"]})
    assert r.json()["revoked"] == 1
    assert (await m.raw("tools/list")).status_code == 401

    # Monitoring: minute aggregates; a high error rate tells Responsible (once an hour).
    owner_conn.execute("""insert into mcp_log (server, user_id, method, tool, duration_ms, status, ts)
                          select 'lejedata', null, 'tools/call', 'soeg_lejemaal', 100 + g, case when g % 4 = 0 then 'error' else 'ok' end,
                                 date_trunc('minute', now()) - interval '2 minutes'
                          from generate_series(1, 40) g""")
    out = await jobs.HANDLERS["developer.metrics"]({}, {})
    assert out["alerts"] == 1
    mon = (await admin.get(f"{DEV}/mcp/lejedata/monitor")).json()
    assert mon["callsToday"] >= 40 and mon["errorRate"] > 5 and mon["p95"] is not None and mon["alerts"][0]["kind"] == "errors"
    notes = (await admin.get(f"{DEV}/notifications")).json()["items"]
    assert any(n["kind"] == "alarm" for n in notes)
    assert (await jobs.HANDLERS["developer.metrics"]({}, {}))["alerts"] == 0          # not again within the hour
