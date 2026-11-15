"""Action-studie (specs/developer/ACTIONS.md): the expression language, cron, the definition rules, and the engine —
runs that resume after a wait, every kind of trigger, approvals answered by logged-in users, access and audit."""

import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

from app import jobs, webhooks
from app.devkit import expr, flow_runs, flows
from tests.helpers import full_user, otp_login, stepup

DEV = "/api/admin/dev"


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _developer(make_client, owner_conn, phone="30000001"):
    c = make_client()
    auth = await full_user(c, phone)
    owner_conn.execute("update users set role = 'admin', admin_roles = '{udvikler}', audience = 'internal' where phone = %s", (f"+45{phone}",))
    return c, auth


async def _call(client, auth, method, url, body=None):
    send = getattr(client, method)
    r = await (send(url, json=body) if body is not None else send(url))
    if r.status_code == 403 and r.headers.get("x-gaer-error") in ("stepup_required", "mfa_required"):
        await stepup(client, auth)
        r = await (send(url, json=body) if body is not None else send(url))
    return r


async def _flow(dev, auth, slug: str, definition: dict, *, trigger="manual", publish=True) -> int:
    r = await _call(dev, auth, "post", f"{DEV}/flows", {"slug": slug, "name": slug, "trigger": trigger})
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    r = await _call(dev, auth, "put", f"{DEV}/flows/{fid}", {"definition": definition})
    assert r.status_code == 200, r.text
    if publish:
        r = await _call(dev, auth, "post", f"{DEV}/artifacts/{fid}/submit", {})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "published", r.text
    return fid


async def _run(dev, auth, fid: int, body: dict | None = None, *, draft=False) -> int:
    r = await _call(dev, auth, "post", f"{DEV}/flows/{fid}/run", {"input": body or {}, "draft": draft})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


async def _detail(dev, run_id: int) -> dict:
    r = await dev.get(f"{DEV}/flows/runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _due_now(owner_conn) -> None:
    """Waiting runs come back when their job's time comes: in tests we move the clock instead of sleeping."""
    owner_conn.execute("update jobs set run_at = now() where status = 'queued'")


@pytest.fixture
def http(monkeypatch):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/kunder":
            return httpx.Response(200, json={"count": 2, "items": [{"id": 1, "navn": "Anna", "aktiv": True}, {"id": 2, "navn": "Bo", "aktiv": False}]})
        if request.url.path == "/fejl":
            return httpx.Response(503, text="nede")
        if request.url.path == "/echo":
            return httpx.Response(200, json={"method": request.method, "body": json.loads(request.content or b"null"),
                                             "auth": request.headers.get("authorization"), "q": dict(request.url.params)})
        return httpx.Response(404, json={"error": "ukendt"})

    monkeypatch.setattr(flow_runs, "transport", httpx.MockTransport(handler))
    return calls


# ── The expression language ─────────────────────────────────────────────────

def test_expressions():
    ctx = {"trigger": {"body": {"navn": "Anna", "items": [{"id": 1, "aktiv": True, "n": 2.5}, {"id": 2, "aktiv": False, "n": 1}]}},
           "steps": {"hent": {"status": 200}}, "vars": {"x": None}}
    cases = {
        "Hej {{ trigger.body.navn }}!": "Hej Anna!",
        "{{ trigger.body.items }}": ctx["trigger"]["body"]["items"],
        "{{ length(trigger.body.items) > 1 and steps.hent.status == 200 }}": True,
        "{{ filter(trigger.body.items, 'item.aktiv') }}": [{"id": 1, "aktiv": True, "n": 2.5}],
        "{{ map(trigger.body.items, 'item.id * 10') }}": [10, 20],
        "{{ trigger.body.mangler.dybt.nede }}": None,
        "{{ vars.x ?? 'standard' }}": "standard",
        "{{ 1 + 2 * 3 }} kr": "7 kr",
        "{{ steps.hent.status >= 400 ? 'fejl' : 'ok' }}": "ok",
        "{{ sum(map(trigger.body.items, 'item.n')) }}": 3.5,
        "{{ join(map(trigger.body.items, 'item.id'), ';') }}": "1;2",
        "{{ 'a' in ['a', 'b'] and not empty(trigger.body.navn) }}": True,
        "{{ formatDate('2026-01-31T08:00:00+00:00', 'dd-MM-yyyy HH:mm') }}": "31-01-2026 09:00",
        "{{ trigger.body.items[1].id }}": 2,
        "{{ {'a': 1, 'b': [1, 2]} }}": {"a": 1, "b": [1, 2]},
        "{{ csv(trigger.body.items, ';') }}": "id;aktiv;n\n1;true;2.5\n2;false;1\n",
        "{{ if(true, 'ja', 1 / 0) }}": "ja",
        "{{ groupBy(trigger.body.items, 'item.aktiv') }}": {"true": [{"id": 1, "aktiv": True, "n": 2.5}], "false": [{"id": 2, "aktiv": False, "n": 1}]},
        "{{ trigger.body.navn }} og {{ steps.hent.status }}": "Anna og 200",
    }
    for text, want in cases.items():
        assert expr.render(text, ctx) == want, text
    assert expr.render({"a": "{{ 1 + 1 }}", "b": ["x {{ trigger.body.navn }}"]}, ctx) == {"a": 2, "b": ["x Anna"]}
    for bad in ("{{ 1 / 0 }}", "{{ foo( }}", "{{ ukendt(1) }}", "{{ __import__('os') }}", "{{ 'a' - 1 }}", "{{ " + "(" * 70 + "1" + ")" * 70 + " }}"):
        with pytest.raises(expr.ExprError):
            expr.render(bad, ctx)
    expr.check("x {{ 1 + 1 }} y")
    with pytest.raises(expr.ExprError):
        expr.check("x {{ 1 + }} y")


def test_cron():
    at = datetime(2026, 1, 9, 12, 7, tzinfo=timezone.utc)   # a Friday
    assert flows.next_run("0 7 * * 1-5", "Europe/Copenhagen", at) == datetime(2026, 1, 12, 6, 0, tzinfo=timezone.utc)
    assert flows.next_run("*/15 * * * *", "UTC", at) == datetime(2026, 1, 9, 12, 15, tzinfo=timezone.utc)
    assert flows.next_run("0 3 1 * *", "UTC", at) == datetime(2026, 2, 1, 3, 0, tzinfo=timezone.utc)
    assert flows.next_run("30 6 * * 7", "UTC", at) == datetime(2026, 1, 11, 6, 30, tzinfo=timezone.utc)
    for bad in ("0 7 * *", "60 * * * *", "0 7 * * 8", "a b c d e", "*/0 * * * *"):
        with pytest.raises(flows.FlowError):
            flows.parse_cron(bad)
    with pytest.raises(flows.FlowError):
        flows.tz_of("Mars/Olympus")


def test_definition_rules():
    ok = flows.validate({"trigger": {"type": "manual", "inputs": {"navn": {"type": "string", "required": True}}},
                         "steps": [{"id": "a", "action": "compose", "with": {"value": "{{ trigger.body.navn }}"}}]})
    assert ok["trigger"]["inputs"]["navn"]["required"] and ok["steps"][0]["retry"] == {"count": 0, "delay_s": 10}
    assert ok["settings"] == flows.SETTINGS_DEFAULT
    bad = [
        ({"trigger": {"type": "x"}, "steps": []}, "Udløseren"),
        ({"trigger": {"type": "schedule", "cron": "nej"}, "steps": []}, "fem felter"),
        ({"trigger": {"type": "event", "events": ["Ikke.En.Hændelse!"]}, "steps": []}, "Ukendt hændelse"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "A", "action": "compose", "with": {"value": 1}}]}, "id'et"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "compose", "with": {"value": 1}}, {"id": "a", "action": "compose", "with": {"value": 1}}]}, "brugt før"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "http", "with": {}}]}, "adresse mangler"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "compose", "with": {"value": "{{ 1 + }}"}}]}, "Udtryk"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "if", "then": []}]}, "betingelse"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "foreach", "items": "{{ [1] }}", "steps": []}]}, "ingen trin"),
        ({"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "compose", "with": {"value": 1}, "retry": {"count": 99}}]}, "retry.count"),
        ({"trigger": {"type": "manual"}, "steps": [], "settings": {"timeout_s": 1}}, "timeout_s"),
    ]
    for definition, text in bad:
        with pytest.raises(HTTPException) as e:
            flows.validate(definition)
        assert text in e.value.detail, (text, e.value.detail)


# ── Runs ────────────────────────────────────────────────────────────────────

async def test_a_run_with_control_flow_http_and_variables(make_client, owner_conn, http):
    dev, auth = await _developer(make_client, owner_conn)
    fid = await _flow(dev, auth, "kundeliste", {
        "trigger": {"type": "manual", "inputs": {"kilde": {"type": "string", "required": True}}},
        "steps": [
            {"id": "hent", "action": "http", "with": {"method": "GET", "url": "http://api.test/{{ trigger.body.kilde }}", "query": {"side": 1}}},
            {"id": "antal", "action": "set", "with": {"name": "antal", "value": "{{ steps.hent.body.count }}"}},
            {"id": "hvis", "action": "if", "when": "{{ vars.antal > 1 }}",
             "then": [{"id": "ja", "action": "compose", "with": {"value": "mange"}}], "else": [{"id": "nej", "action": "compose", "with": {"value": "få"}}]},
            {"id": "loop", "action": "foreach", "items": "{{ filter(steps.hent.body.items, 'item.aktiv') }}",
             "steps": [{"id": "send", "action": "http", "with": {"method": "POST", "url": "http://api.test/echo", "body": {"id": "{{ item.id }}", "nr": "{{ index }}"}}}]},
            {"id": "valg", "action": "switch", "value": "{{ steps.hvis.branch }}", "cases": {"then": [{"id": "t", "action": "compose", "with": {"value": 1}}]}, "default": []},
            {"id": "skip", "action": "compose", "when": "{{ false }}", "with": {"value": "aldrig"}},
            {"id": "resultat", "action": "compose", "with": {"value": {"antal": "{{ vars.antal }}", "sendt": "{{ steps.loop.count }}", "gren": "{{ steps.hvis.branch }}"}}},
        ]})
    # The declared input is required.
    r = await _call(dev, auth, "post", f"{DEV}/flows/{fid}/run", {"input": {}})
    assert r.status_code == 422 and "kilde" in r.json()["detail"]
    run_id = await _run(dev, auth, fid, {"kilde": "kunder"})
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "succeeded", d
    assert d["output"] == {"antal": 2, "sendt": 1, "gren": "then"}
    by_id = {s["step_id"]: s for s in d["steps"]}
    assert by_id["hent"]["status"] == "succeeded" and by_id["hent"]["input"]["url"] == "http://api.test/kunder"
    assert by_id["hent"]["output"]["body"]["count"] == 2 and by_id["send"]["output"]["body"]["body"] == {"id": 1, "nr": 0}
    assert by_id["skip"]["status"] == "skipped" and "nej" not in by_id and by_id["t"]["status"] == "succeeded"
    assert str(http[0].url) == "http://api.test/kunder?side=1" and http[0].headers["user-agent"] == "Gaer-flow/1"
    assert owner_conn.execute("select count(*) from audit_log where action = 'dev.flow.run' and detail->>'run_id' = %s", (str(run_id),)).fetchone()[0] == 1
    listed = (await dev.get(f"{DEV}/flows")).json()["items"]
    assert listed[0]["slug"] == "kundeliste" and listed[0]["lastRun"]["status"] == "succeeded" and listed[0]["steps"] == 11


async def test_failure_retry_on_error_and_rerun(make_client, owner_conn, http):
    dev, auth = await _developer(make_client, owner_conn)
    fid = await _flow(dev, auth, "fejlflow", {
        "trigger": {"type": "manual"},
        "steps": [
            {"id": "blod", "action": "http", "with": {"url": "http://api.test/fejl"}, "on_error": "continue"},
            {"id": "haard", "action": "http", "with": {"url": "http://api.test/fejl"}, "retry": {"count": 1, "delay_s": 60}},
            {"id": "aldrig", "action": "compose", "with": {"value": 1}},
        ],
        "on_error": [{"id": "ryd", "action": "compose", "with": {"value": "fejl i {{ error.step }}: {{ error.message }}"}}]})
    run_id = await _run(dev, auth, fid)
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "waiting" and d["waiting"]["kind"] == "retry", d
    _due_now(owner_conn)
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "failed" and d["error"].startswith("haard:") and "503" in d["error"]
    attempts = [(s["step_id"], s["attempt"], s["status"]) for s in d["steps"]]
    assert ("blod", 1, "failed") in attempts and ("haard", 1, "failed") in attempts and ("haard", 2, "failed") in attempts
    assert ("ryd", 1, "succeeded") in attempts and "aldrig" not in [a[0] for a in attempts]
    ryd = next(s for s in d["steps"] if s["step_id"] == "ryd")
    assert ryd["output"].startswith("fejl i haard: ")
    assert len(http) == 3
    # Responsible is told, and the webhook event exists.
    assert owner_conn.execute("select count(*) from dev_notifications where kind = 'flow_failed'").fetchone()[0] == 1
    assert "flow.run.failed" in webhooks.EVENTS
    # Run again with the same trigger data, from the run page.
    r = await _call(dev, auth, "post", f"{DEV}/flows/runs/{run_id}/rerun")
    assert r.status_code == 200
    again = await _detail(dev, r.json()["run_id"])
    assert again["trigger"] == "rerun"


async def test_delay_resumes_and_cancel(make_client, owner_conn):
    dev, auth = await _developer(make_client, owner_conn)
    fid = await _flow(dev, auth, "vent", {
        "trigger": {"type": "manual"},
        "steps": [{"id": "foer", "action": "compose", "with": {"value": "{{ now() }}"}},
                  {"id": "vent", "action": "delay", "with": {"seconds": 600}},
                  {"id": "efter", "action": "compose", "with": {"value": "færdig"}}]})
    run_id = await _run(dev, auth, fid)
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "waiting" and d["waiting"]["kind"] == "delay" and [s["step_id"] for s in d["steps"]] == ["foer", "vent"]
    # Nothing happens before the time; then the run resumes exactly where it stopped.
    await jobs.run_pending()
    assert (await _detail(dev, run_id))["status"] == "waiting"
    owner_conn.execute("update dev_flow_runs set state = jsonb_set(state, '{waits}', %s) where id = %s",
                       (json.dumps({"1": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}), run_id))
    _due_now(owner_conn)
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "succeeded" and d["output"] == "færdig"
    assert [s["step_id"] for s in d["steps"]] == ["foer", "vent", "efter"] and len(d["steps"]) == 3
    # A waiting run can be cancelled; it then goes no further.
    run2 = await _run(dev, auth, fid)
    await jobs.run_pending()
    r = await _call(dev, auth, "post", f"{DEV}/flows/runs/{run2}/cancel")
    assert r.status_code == 200
    _due_now(owner_conn)
    await jobs.run_pending()
    d = await _detail(dev, run2)
    assert d["status"] == "cancelled" and len(d["steps"]) == 2
    assert (await _call(dev, auth, "post", f"{DEV}/flows/runs/{run2}/cancel")).status_code == 409


async def test_approval_by_a_logged_in_approver(make_client, owner_conn, capsys):
    dev, auth = await _developer(make_client, owner_conn)
    chef = make_client()
    await otp_login(chef, "30000002")
    owner_conn.execute("update users set email = 'chef@firma.dk' where phone = '+4530000002'")
    other = make_client()
    await otp_login(other, "30000003")
    fid = await _flow(dev, auth, "godkend", {
        "trigger": {"type": "manual", "inputs": {"beloeb": {"type": "number"}}},
        "steps": [{"id": "ok", "action": "approval", "with": {"approvers": ["chef@firma.dk"], "title": "Udgift {{ trigger.body.beloeb }} kr",
                                                               "message": "Må vi?", "data": {"beloeb": "{{ trigger.body.beloeb }}"}, "timeout_hours": 24}},
                  {"id": "svar", "action": "compose", "with": {"value": "{{ steps.ok.approved ? 'købt' : 'droppet' }}"}}]})
    run_id = await _run(dev, auth, fid, {"beloeb": 1200})
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "waiting" and d["waiting"]["kind"] == "approval" and d["approvals"][0]["status"] == "pending"
    link = re.search(r"/flow-godkendelse/(\S+)", capsys.readouterr().out)
    assert link, "the approver gets a mail with the link"
    token = link.group(1)
    # Only the approver sees and decides it; test accounts and strangers do not.
    assert (await other.get(f"/api/flows/approvals/{token}")).status_code == 404
    assert (await other.post(f"/api/flows/approvals/{token}", json={"approve": True})).status_code == 403
    assert (await make_client().get(f"/api/flows/approvals/{token}")).status_code == 401
    r = await chef.get(f"/api/flows/approvals/{token}")
    assert r.status_code == 200 and r.json()["title"] == "Udgift 1200 kr" and r.json()["data"] == {"beloeb": 1200} and r.json()["mine"]
    r = await chef.post(f"/api/flows/approvals/{token}", json={"approve": True, "comment": "Fint"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (await chef.post(f"/api/flows/approvals/{token}", json={"approve": False})).status_code == 409
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "succeeded" and d["output"] == "købt"
    ok = next(s for s in d["steps"] if s["step_id"] == "ok" and s["status"] == "succeeded")
    assert ok["output"]["approved"] is True and ok["output"]["comment"] == "Fint"
    assert owner_conn.execute("select count(*) from audit_log where action in ('flow.approval.requested', 'flow.approval.decided')").fetchone()[0] == 2
    # An approval nobody answers expires: the flow goes on with approved = false.
    run2 = await _run(dev, auth, fid, {"beloeb": 5})
    await jobs.run_pending()
    owner_conn.execute("update dev_flow_approvals set expires_at = now() - interval '1 minute' where run_id = %s", (run2,))
    _due_now(owner_conn)
    await jobs.run_pending()
    d = await _detail(dev, run2)
    assert d["status"] == "succeeded" and d["output"] == "droppet" and d["approvals"][0]["status"] == "expired"
    # GDPR: the approver's export lists the approvals they were asked for; an erased approver leaves the lists.
    from app import gdpr
    from app.logs import RequestMeta
    chef_id = owner_conn.execute("select id from users where phone = '+4530000002'").fetchone()[0]
    from app import db
    async with db.connection() as conn:
        export = await gdpr.build_user_export(conn, chef_id)
    assert [(a["title"], a["status"], a["decided_by_me"]) for a in export["flow_approvals"]] == [("Udgift 1200 kr", "approved", True), ("Udgift 5 kr", "expired", False)]
    await gdpr.erase_user(chef_id, actor_id=chef_id, meta=RequestMeta(), reason="test")
    assert owner_conn.execute("select bool_and(not (%s = any(approvers))) and bool_and(decided_by is null) from dev_flow_approvals", (chef_id,)).fetchone()[0]


async def test_event_trigger_child_flow_and_depth(make_client, owner_conn, http):
    dev, auth = await _developer(make_client, owner_conn)
    child = await _flow(dev, auth, "barn", {
        "trigger": {"type": "manual", "inputs": {"id": {"type": "number", "required": True}}},
        "steps": [{"id": "ud", "action": "compose", "with": {"value": "bruger {{ trigger.body.id }}"}}]})
    parent = await _flow(dev, auth, "ved_ny_bruger", {
        "trigger": {"type": "event", "events": ["user.created"]},
        "steps": [{"id": "kald", "action": "flow", "with": {"flow": "barn", "input": {"id": "{{ trigger.data.user_id }}"}, "wait": True}},
                  {"id": "haendelse", "action": "event", "with": {"name": "velkomst", "data": {"user_id": "{{ trigger.data.user_id }}"}}},
                  {"id": "ud", "action": "compose", "with": {"value": "{{ steps.kald.output }}"}}]}, trigger="event")
    assert owner_conn.execute("select kind, key from dev_flow_subscriptions where artifact_id = %s", (parent,)).fetchall() == [("webhook_event", "user.created")]
    # A new user logs in → user.created → the flow starts in the same transaction, as a job.
    newcomer = make_client()
    await otp_login(newcomer, "30000009")
    uid = owner_conn.execute("select id from users where phone = '+4530000009'").fetchone()[0]
    runs = owner_conn.execute("select id, trigger, trigger_data from dev_flow_runs where artifact_id = %s", (parent,)).fetchall()
    assert len(runs) == 1 and runs[0][1] == "event" and runs[0][2]["data"]["user_id"] == uid
    await jobs.run_pending()           # parent starts the child and waits; the child runs and wakes the parent
    for _ in range(3):
        _due_now(owner_conn)
        await jobs.run_pending()
    d = await _detail(dev, runs[0][0])
    assert d["status"] == "succeeded", d
    assert d["output"] == f"bruger {uid}" and d["children"][0]["slug"] == "barn" and d["children"][0]["depth"] == 1
    # The parent's own event did not start it again, and the event went to the webhook log.
    assert owner_conn.execute("select count(*) from dev_flow_runs where artifact_id = %s", (parent,)).fetchone()[0] == 1
    assert "flow.event" in webhooks.EVENTS


async def test_schedule_and_webhook_triggers(make_client, owner_conn):
    dev, auth = await _developer(make_client, owner_conn)
    fid = await _flow(dev, auth, "morgen", {"trigger": {"type": "schedule", "cron": "0 7 * * 1-5", "tz": "Europe/Copenhagen"},
                                            "steps": [{"id": "a", "action": "compose", "with": {"value": "{{ trigger.scheduled_for }}"}}]}, trigger="schedule")
    nxt = owner_conn.execute("select next_run_at from dev_flow_schedule where artifact_id = %s", (fid,)).fetchone()[0]
    assert nxt is not None and nxt > datetime.now(timezone.utc)
    detail = (await dev.get(f"{DEV}/flows/{fid}")).json()
    assert detail["triggerText"] == "Hverdage kl. 08:00" or detail["triggerText"].startswith("Cron")
    assert await flow_runs.schedule_job({}, {}) == {"started": []}
    owner_conn.execute("update dev_flow_schedule set next_run_at = now() - interval '1 minute' where artifact_id = %s", (fid,))
    result = await flow_runs.schedule_job({}, {})
    assert len(result["started"]) == 1
    assert owner_conn.execute("select next_run_at > now() from dev_flow_schedule where artifact_id = %s", (fid,)).fetchone()[0]
    await jobs.run_pending()
    d = await _detail(dev, result["started"][0])
    assert d["status"] == "succeeded" and d["trigger"] == "schedule" and d["output"]
    # A disabled flow is not started by its schedule.
    assert (await _call(dev, auth, "patch", f"{DEV}/artifacts/{fid}", {"enabled": False})).status_code == 200
    owner_conn.execute("update dev_flow_schedule set next_run_at = now() - interval '1 minute' where artifact_id = %s", (fid,))
    assert await flow_runs.schedule_job({}, {}) == {"started": []}

    # Webhook: the endpoint is made from the studio (step-up) and the flow cannot be published without it.
    r = await _call(dev, auth, "post", f"{DEV}/flows", {"slug": "indkommende", "trigger": "webhook"})
    wid = r.json()["id"]
    definition = {"trigger": {"type": "webhook"}, "steps": [{"id": "a", "action": "compose", "with": {"value": "{{ trigger.body.ordre }} fra {{ trigger.headers['x-gaer-event'] }}"}}]}
    assert (await _call(dev, auth, "put", f"{DEV}/flows/{wid}", {"definition": definition})).status_code == 200
    r = await _call(dev, auth, "post", f"{DEV}/artifacts/{wid}/submit", {})
    assert r.status_code == 409 and "endpoint" in r.json()["detail"]
    ep = (await _call(dev, auth, "post", f"{DEV}/flows/{wid}/webhook-endpoint")).json()
    assert ep["secret"].startswith("whsec_") and ep["url"].endswith(ep["key"])
    definition["trigger"]["endpoint_key"] = ep["key"]
    assert (await _call(dev, auth, "put", f"{DEV}/flows/{wid}", {"definition": definition})).status_code == 200
    assert (await _call(dev, auth, "post", f"{DEV}/artifacts/{wid}/submit", {})).json()["status"] == "published"
    sender = make_client()
    sender.headers.pop("x-gaer")
    body = json.dumps({"ordre": 42}).encode()
    ts = int(time.time())
    r = await sender.post(f"/api/hooks/in/{ep['key']}", content=body, headers={"content-type": "application/json", "x-gaer-event": "ordre.ny",
                                                                               "x-gaer-signature": f"t={ts},v1={webhooks.sign(ep['secret'], ts, body)}"})
    assert r.status_code == 202, r.text
    assert (await sender.post(f"/api/hooks/in/{ep['key']}", content=body)).status_code == 401
    await jobs.run_pending()   # the inbound job starts the flow …
    await jobs.run_pending()   # … and the flow runs
    runs = (await dev.get(f"{DEV}/flows/runs", params={"flow": wid})).json()["items"]
    assert len(runs) == 1 and runs[0]["status"] == "succeeded" and runs[0]["trigger"] == "webhook"
    assert (await _detail(dev, runs[0]["id"]))["output"] == "42 fra ordre.ny"


async def test_http_guards_mail_and_draft_test_runs(make_client, owner_conn, http, capsys):
    dev, auth = await _developer(make_client, owner_conn)
    # Auth headers in the definition are refused; the address must pass the SSRF guard (private hosts are allowed in tests).
    fid = await _flow(dev, auth, "vagt", {"trigger": {"type": "manual"}, "steps": [
        {"id": "a", "action": "http", "with": {"url": "http://api.test/echo", "headers": {"Authorization": "Bearer hemmelig"}}, "on_error": "continue"},
        {"id": "b", "action": "http", "with": {"url": "ftp://api.test/x"}, "on_error": "continue"},
        {"id": "m", "action": "mail", "with": {"to": "nogen@firma.dk, {{ trigger.body.user }}", "subject": "Rapport {{ today() }}", "text": "Hej\n\nAlt vel."}},
        {"id": "f", "action": "file", "with": {"path": "/shared/rapporter/", "filename": "rapport.csv", "content": "{{ csv([{'a': 1}]) }}", "content_type": "text/csv"}},
    ]})
    me = (await dev.get("/api/auth/me")).json()["user"]["id"]
    owner_conn.execute("update users set email = 'udvikler@firma.dk' where id = %s", (me,))
    run_id = await _run(dev, auth, fid, {"user": me})
    await jobs.run_pending()
    d = await _detail(dev, run_id)
    assert d["status"] == "succeeded", d
    by_id = {s["step_id"]: s for s in d["steps"]}
    assert "nøgler" in by_id["a"]["error"] and "https" in by_id["b"]["error"] and len(http) == 0
    assert by_id["m"]["output"]["sent"] == 2 and "nogen@firma.dk" in capsys.readouterr().out
    assert by_id["f"]["output"]["path"] == "/shared/rapporter/rapport.csv"
    assert owner_conn.execute("select count(*) from files where space = 'shared' and filename = 'rapport.csv'").fetchone()[0] == 1
    assert owner_conn.execute("select count(*) from audit_log where action = 'flow.mail.sent'").fetchone()[0] == 1
    # A draft is tested without being published: the run is marked as a test and never counts as the flow's last run.
    r = await _call(dev, auth, "put", f"{DEV}/flows/{fid}", {"definition": {"trigger": {"type": "manual"}, "steps": [{"id": "x", "action": "compose", "with": {"value": "kladde"}}]}})
    assert r.status_code == 200 and r.json()["version"]["status"] == "draft"
    test_run = await _run(dev, auth, fid, draft=True)
    await jobs.run_pending()
    d = await _detail(dev, test_run)
    assert d["status"] == "succeeded" and d["output"] == "kladde" and d["is_test"] and d["trigger"] == "test"
    listed = next(f for f in (await dev.get(f"{DEV}/flows")).json()["items"] if f["id"] == fid)
    assert listed["lastRun"]["id"] == run_id and listed["draft"] == "draft"
    # Expressions can be tried against a run's data from the editor.
    r = await dev.post(f"{DEV}/flows/expr", json={"expression": "{{ steps.m.sent + 1 }}", "run_id": run_id})
    assert r.json() == {"ok": True, "value": 3, "type": "tal", "truncated": False}
    assert (await dev.post(f"{DEV}/flows/expr", json={"expression": "{{ 1 / 0 }}"})).json()["ok"] is False


async def test_access_and_catalog(make_client, owner_conn):
    dev, auth = await _developer(make_client, owner_conn)
    user = make_client()
    await otp_login(user, "30000004")
    assert (await user.get(f"{DEV}/flows")).status_code == 403
    assert (await user.get(f"{DEV}/flows/catalog")).status_code == 403
    cat = (await dev.get(f"{DEV}/flows/catalog")).json()
    assert {a["key"] for a in cat["actions"]} == set(flows.ACTIONS) and cat["triggers"][0]["key"] == "manual"
    assert any(e["key"] == "user.created" for e in cat["events"]) and cat["functions"] and cat["cron_presets"]
    fid = await _flow(dev, auth, "mit", {"trigger": {"type": "manual"}, "steps": [{"id": "a", "action": "compose", "with": {"value": 1}}]}, publish=False)
    # Another developer who is not an owner cannot change the draft; a Udvikler-admin can.
    dev2, auth2 = await _developer(make_client, owner_conn, "30000005")
    r = await _call(dev2, auth2, "put", f"{DEV}/flows/{fid}", {"definition": {"trigger": {"type": "manual"}, "steps": []}})
    assert r.status_code == 403
    owner_conn.execute("update users set admin_roles = '{udvikler_admin}' where phone = '+4530000005'")
    r = await _call(dev2, auth2, "put", f"{DEV}/flows/{fid}", {"definition": {"trigger": {"type": "manual"}, "steps": []}})
    assert r.status_code == 200
    # Running needs a published version; publishing needs steps.
    assert (await _call(dev, auth, "post", f"{DEV}/flows/{fid}/run", {"input": {}})).status_code == 409
    r = await _call(dev, auth, "post", f"{DEV}/artifacts/{fid}/submit", {})
    assert r.status_code == 409 and "ingen trin" in r.json()["detail"]
    assert (await dev.get(f"{DEV}/flows/999999")).status_code == 404
