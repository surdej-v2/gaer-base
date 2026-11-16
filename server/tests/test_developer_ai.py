"""AI in Gær Developer, fase 0 (specs/developer/AI.md): the shared check (AI-3), the context the server builds from
{kind, id} with metadata only (AI-1), the developer chat, the tools, and suggestions that work without AI (AI-4) with
dismissals that are audited and part of the GDPR export (AI-5)."""

import dataclasses
import json

import httpx
import pytest

from app import ai, config, db, gdpr
from app.devkit import ai_context, assist
from tests.helpers import full_user
from tests.test_ai import events as sse_events
from tests.test_ai import sse_lines

AI = "/api/admin/dev/ai"
sent: list[dict] = []


@pytest.fixture
def ai_on(monkeypatch):
    sent.clear()
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, ai_api_key="test-key"))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body.get("stream"):
            return httpx.Response(200, content=sse_lines([{"choices": [{"delta": {"content": "Kørslen ramte grænsen."}}]},
                                                          {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 5}}]),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Forslag"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    monkeypatch.setattr(ai, "transport", httpx.MockTransport(handler))


def _uid(owner_conn, phone="+4520000001") -> int:
    return owner_conn.execute("select id from users where phone = %s", (phone,)).fetchone()[0]


def _seed(owner_conn) -> dict:
    """A pipeline whose last two runs failed (with a value quoted in the error), a dataset without a description, a
    published MCP endpoint without testcases and a connection whose definition holds a host."""
    ids = {}
    ids["pipeline"] = owner_conn.execute("""
        insert into dev_artifacts (type, slug, name) values ('pipeline', 'lejemaal_pr_ejendom', 'Lejemål pr. ejendom')
        returning id""").fetchone()[0]
    v = owner_conn.execute("""
        insert into dev_artifact_versions (artifact_id, major, minor, patch, change_type, status, definition, contract)
        values (%s, 1, 3, 2, 'initial', 'published', %s, %s) returning id""",
        (ids["pipeline"], json.dumps({"stages": [{"source": "lejemaal"}, {"match": {"status": "aktiv"}}]}),
         json.dumps({"fields": {"aarlig_leje": {"type": "number", "classification": "yellow"},
                                "lejer_navn": {"type": "text", "classification": "red", "personal": True}}}))).fetchone()[0]
    owner_conn.execute("update dev_artifacts set current_version_id = %s where id = %s", (v, ids["pipeline"]))
    runs = []
    for status, error in (("succeeded", None), ("failed", "Timeout: kørslen tog længere end den maksimale køretid."),
                          ("failed", 'Databasefejl: invalid input syntax for type integer: "Lejer 7"')):
        runs.append(owner_conn.execute("""
            insert into dev_pipeline_runs (artifact_id, version_id, trigger, status, error) values (%s, %s, 'manual', %s, %s)
            returning id""", (ids["pipeline"], v, status, error)).fetchone()[0])
    ids["run"] = runs[-1]
    owner_conn.execute("""
        insert into dev_pipeline_run_phases (run_id, phase, label, started_at, ms, detail)
        values (%s, 'lookup', '$lookup lejere', now(), 252000, 'stoppede ved ''Vesterbrogade 12''')""", (ids["run"],))
    ids["dataset"] = owner_conn.execute("""
        insert into datasets (key, name, description) values ('lejere_ai_test', 'Lejere', '') returning id""").fetchone()[0]
    ids["mcp"] = owner_conn.execute("""
        insert into dev_artifacts (type, slug, name) values ('mcp_endpoint', 'ejendomme', 'Ejendomme') returning id""").fetchone()[0]
    mv = owner_conn.execute("""
        insert into dev_artifact_versions (artifact_id, major, minor, patch, change_type, status, definition, contract)
        values (%s, 1, 0, 0, 'initial', 'published', '{}', '{}') returning id""", (ids["mcp"],)).fetchone()[0]
    owner_conn.execute("update dev_artifacts set current_version_id = %s where id = %s", (mv, ids["mcp"]))
    ids["connection"] = owner_conn.execute("""
        insert into dev_artifacts (type, slug, name) values ('connection', 'crm', 'CRM') returning id""").fetchone()[0]
    cv = owner_conn.execute("""
        insert into dev_artifact_versions (artifact_id, major, minor, patch, change_type, status, definition, contract)
        values (%s, 1, 0, 0, 'initial', 'published', %s, '{}') returning id""",
        (ids["connection"], json.dumps({"host": "db.hemmelig.example", "user": "crm_reader"}))).fetchone()[0]
    owner_conn.execute("update dev_artifacts set current_version_id = %s where id = %s", (cv, ids["connection"]))
    return ids


async def _admin(make_client):
    admin = make_client()
    await full_user(admin, "20000001")
    return admin


# ── AI-4: suggestions without AI, dismissals ────────────────────────────────

async def test_suggestions_work_without_ai_and_dismissals_are_audited(make_client, owner_conn):
    admin = await _admin(make_client)
    ids = _seed(owner_conn)
    r = await admin.get(f"{AI}/suggestions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai"] is False
    by_kind = {s["kind"]: s for s in body["items"]}
    failed = by_kind["pipeline_failed"]
    assert failed["key"] == f"pipeline_failed:{ids['pipeline']}:{ids['run']}"
    assert "to gange i træk" in failed["title"]
    assert failed["action"]["path"] == f"/developer/pipeline/koersler/{ids['run']}"
    assert failed["ref"] == {"kind": "run", "id": ids["run"]}
    assert by_kind["mcp_no_testcases"]["action"]["path"] == "/developer/mcp/testbaenk?endpoint=ejendomme"
    assert "mangler en beskrivelse" in by_kind["dataset_no_description"]["title"]

    only = (await admin.get(f"{AI}/suggestions", params={"scope": f"artifact:{ids['pipeline']}"})).json()["items"]
    assert [s["kind"] for s in only] == ["pipeline_failed"]
    assert (await admin.get(f"{AI}/suggestions", params={"scope": "alt"})).status_code == 400

    r = await admin.post(f"{AI}/suggestions/dismiss", json={"key": failed["key"]})
    assert r.status_code == 204
    keys = [s["key"] for s in (await admin.get(f"{AI}/suggestions")).json()["items"]]
    assert failed["key"] not in keys
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.ai.suggestion.dismissed' and row_id = %s",
                              (failed["key"],)).fetchone()[0] == 1
    # dismissing twice is quiet and audited once
    assert (await admin.post(f"{AI}/suggestions/dismiss", json={"key": failed["key"]})).status_code == 204
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.ai.suggestion.dismissed'").fetchone()[0] == 1

    # a new failed run is something new to say: a new key
    new_run = owner_conn.execute("""
        insert into dev_pipeline_runs (artifact_id, trigger, status, error) values (%s, 'manual', 'failed', 'x') returning id""",
        (ids["pipeline"],)).fetchone()[0]
    keys = [s["key"] for s in (await admin.get(f"{AI}/suggestions")).json()["items"]]
    assert f"pipeline_failed:{ids['pipeline']}:{new_run}" in keys

    r = await admin.delete(f"{AI}/suggestions/dismiss/{failed['key']}")
    assert r.status_code == 204
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.ai.suggestion.restored'").fetchone()[0] == 1
    assert (await admin.delete(f"{AI}/suggestions/dismiss/{failed['key']}")).status_code == 404
    assert (await admin.post(f"{AI}/suggestions/dismiss", json={"key": "ikke et forslag"})).status_code == 422


async def test_dismissals_are_in_the_export_and_go_with_the_user(make_client, owner_conn):
    admin = await _admin(make_client)
    await admin.post(f"{AI}/suggestions/dismiss", json={"key": "dataset_no_description:1:1"})
    uid = _uid(owner_conn)
    async with db.connection() as conn:
        export = await gdpr.build_user_export(conn, uid)
    assert [r["key"] for r in export["developer_ai_dismissed_suggestions"]] == ["dataset_no_description:1:1"]
    owner_conn.execute("delete from users where id = %s", (uid,))
    assert owner_conn.execute("select count(*) from dev_ai_dismissals").fetchone()[0] == 0


async def test_only_admins_with_developer_reach_it(make_client):
    user = make_client()
    await full_user(user, "20000001")   # first user: super admin
    other = make_client()
    await full_user(other, "20000002")  # an ordinary user
    assert (await other.get(f"{AI}/suggestions")).status_code in (401, 403)


# ── AI-1: the context holds metadata, never values ──────────────────────────

async def test_context_is_metadata_without_values(make_client, owner_conn):
    admin = await _admin(make_client)
    ids = _seed(owner_conn)
    r = await admin.get(f"{AI}/context", params={"kind": "run", "id": ids["run"], "text": True})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["chips"] == ["Lejemål pr. ejendom", f"Kørsel #{ids['run']}"]
    assert "Lejer 7" not in run["text"] and "Vesterbrogade" not in run["text"]
    assert "«…»" in run["text"] and "$lookup lejere" in run["text"]

    art = (await admin.get(f"{AI}/context", params={"kind": "artifact", "id": ids["pipeline"], "text": True})).json()
    info = json.loads(art["text"])
    assert info["udgivet_version"] == "1.3.2"
    assert {"navn": "lejer_navn", "type": "text", "klassifikation": "red", "persondata": True} in info["kontrakt"]["felter"]
    assert info["definition"]["stages"][1] == {"match": {"status": "aktiv"}}
    assert [k["status"] for k in info["seneste_kørsler"]] == ["failed", "failed", "succeeded"]
    assert "Lejer 7" not in art["text"]

    conn_ctx = (await admin.get(f"{AI}/context", params={"kind": "artifact", "id": ids["connection"], "text": True})).json()
    assert "hemmelig" not in conn_ctx["text"] and "crm_reader" not in conn_ctx["text"]
    assert "text" not in (await admin.get(f"{AI}/context", params={"kind": "overview"})).json()
    assert (await admin.get(f"{AI}/context", params={"kind": "artifact"})).status_code == 400
    assert (await admin.get(f"{AI}/context", params={"kind": "artifact", "id": 999999})).status_code == 404

    found = (await admin.get(f"{AI}/search", params={"q": "lejemål"})).json()["items"]
    assert [(f["kind"], f["name"]) for f in found] == [("artifact", "Lejemål pr. ejendom")]


def test_redact_removes_quoted_values():
    assert ai_context.redact('invalid input syntax for type integer: "Lejer 7"') == "invalid input syntax for type integer: «…»"
    assert ai_context.redact("stoppede ved 'Vesterbrogade 12' og «x»") == "stoppede ved «…» og «…»"


# ── The chat and AI-3 ───────────────────────────────────────────────────────

async def test_chat_is_off_without_ai(make_client, owner_conn):
    admin = await _admin(make_client)
    r = await admin.post(f"{AI}/chat", json={"messages": [{"role": "user", "content": "hej"}]})
    assert r.status_code == 404


async def test_chat_sends_the_server_built_context_and_the_tools(make_client, owner_conn, ai_on):
    admin = await _admin(make_client)
    ids = _seed(owner_conn)
    r = await admin.post(f"{AI}/chat", json={"messages": [{"role": "user", "content": "Hvorfor fejlede den?"}],
                                             "ref": {"kind": "run", "id": ids["run"]}, "page": "/developer/pipeline"})
    assert r.status_code == 200, r.text
    assert [e for e, _ in sse_events(r.text)] == ["delta", "done"]
    body = sent[-1]
    prompt = json.dumps(body["messages"], ensure_ascii=False)
    assert f"Kørsel #{ids['run']} af Lejemål pr. ejendom" in prompt
    assert "Lejer 7" not in prompt and "Gær Developer" in prompt
    assert "[Pipelines](/developer/pipeline)" in prompt or "Pipelines (/developer/pipeline)" in prompt
    names = {t["function"]["name"] for t in body["tools"]}
    assert {"catalog_search", "context_get", "propose_pipeline_change", "propose_testcases"} <= names
    assert "save_clip" not in names
    usage = owner_conn.execute("select surface, prompt_tokens from ai_usage order by id desc limit 1").fetchone()
    assert usage == ("developer", 50)


async def test_the_app_chat_refuses_the_developer_surface(make_client, ai_on):
    admin = await _admin(make_client)
    r = await admin.post("/api/ai/chat", json={"surface": "developer", "messages": [{"role": "user", "content": "hej"}]})
    assert r.status_code == 400


async def test_budget_spent_blocks_the_chat_and_assist(make_client, owner_conn, ai_on):
    admin = await _admin(make_client)
    uid = _uid(owner_conn)
    owner_conn.execute("update users set ai_monthly_tokens = 10 where id = %s", (uid,))
    owner_conn.execute("""insert into ai_usage (user_id, surface, model, prompt_tokens, completion_tokens, estimated)
                          values (%s, 'developer', 'm', 40, 10, false)""", (uid,))
    r = await admin.post(f"{AI}/chat", json={"messages": [{"role": "user", "content": "hej"}]})
    assert r.status_code == 429 and "kvote" in r.json()["detail"]
    assert await assist.complete("system", "bruger", user_id=uid) is None
    assert sent == []
    # a call without a user (a job) only needs AI to be on
    assert await assist.complete("system", "bruger") == "Forslag"
