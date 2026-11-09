"""Captcha on login/sign-up, the job queue with schedules and retention policies, and outgoing webhooks."""

import base64
import json

import httpx
import pytest

from app import captcha, config, jobs, webhooks
from tests.helpers import full_user, otp_login


@pytest.fixture
def captcha_on(monkeypatch):
    monkeypatch.setenv("CAPTCHA", "pow")
    monkeypatch.setenv("CAPTCHA_MAX_NUMBER", "2000")
    config.reload()
    yield
    monkeypatch.setenv("CAPTCHA", "off")
    config.reload()


# ── Captcha ─────────────────────────────────────────────────────────────────

async def test_otp_start_needs_a_solved_captcha(make_client, owner_conn, captcha_on):
    client = make_client()
    assert (await client.get("/api/public/config")).json()["captcha"] is True
    body = {"email": "robot@example.com"}
    r = await client.post("/api/auth/otp/start", json=body)
    assert r.status_code == 400 and "Robottjekket" in r.json()["detail"]

    challenge = (await client.get("/api/auth/captcha")).json()
    assert challenge["enabled"] and challenge["maxnumber"] == 2000
    solution = captcha.solve(challenge)

    # Tampering with the number or the signature fails.
    data = json.loads(base64.b64decode(solution))
    wrong = base64.b64encode(json.dumps({**data, "number": data["number"] + 1}).encode()).decode()
    forged = base64.b64encode(json.dumps({**data, "signature": "0" * 64}).encode()).decode()
    for bad in (wrong, forged, "not-base64!"):
        assert (await client.post("/api/auth/otp/start", json={**body, "captcha": bad})).status_code == 400

    r = await client.post("/api/auth/otp/start", json={**body, "captcha": solution})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    # A solution works once.
    assert (await client.post("/api/auth/otp/start", json={**body, "captcha": solution})).status_code == 400
    r = await client.post("/api/auth/otp/verify", json={**body, "code": code})
    assert r.json()["status"] == "logged_in"

    reasons = [r[0] for r in owner_conn.execute(
        "select detail->>'captcha' from auth_log where event = 'otp.sent' and result = 'fail' order by id")]
    assert reasons == ["missing", "wrong", "signature", "malformed", "reused"]


async def test_expired_captcha_is_refused(make_client, monkeypatch, captcha_on):
    client = make_client()
    challenge = (await client.get("/api/auth/captcha")).json()
    solution = captcha.solve(challenge)
    monkeypatch.setattr(captcha.time, "time", lambda: challenge["expires"] + 1)
    r = await client.post("/api/auth/otp/start", json={"email": "late@example.com", "captcha": solution})
    assert r.status_code == 400


# ── Jobs ────────────────────────────────────────────────────────────────────

async def test_job_retries_with_backoff_then_dies(owner_conn):
    from app import db
    async with db.connection() as conn:
        ok_id = await jobs.enqueue(conn, "noop", {})
        bad_id = await jobs.enqueue(conn, "noop", {"fail": True}, max_attempts=2)
        assert await jobs.enqueue(conn, "noop", {}, dedupe_key="one") is not None
        assert await jobs.enqueue(conn, "noop", {}, dedupe_key="one") is None  # deduplicated while queued
    assert await jobs.run_pending() == 3
    rows = dict(owner_conn.execute("select id, status from jobs where id = any(%s)", ([ok_id, bad_id],)).fetchall())
    assert rows == {ok_id: "succeeded", bad_id: "queued"}
    assert owner_conn.execute("select run_at > now() + interval '20 seconds' from jobs where id = %s",
                              (bad_id,)).fetchone()[0]
    owner_conn.execute("update jobs set run_at = now() where id = %s", (bad_id,))
    await jobs.run_pending()
    status, attempts, error = owner_conn.execute("select status, attempts, last_error from jobs where id = %s",
                                                 (bad_id,)).fetchone()
    assert (status, attempts) == ("dead", 2) and "med vilje" in error


async def test_schedules_and_retention_policies(make_client, owner_conn):
    admin = make_client()
    await full_user(admin, "71111111")
    owner_conn.execute("update job_schedules set next_run_at = now() - interval '1 minute'")
    # maintenance, retention.logs, retention.cleanup, knowledge.bin, mail.retention, mail.sync, mail.backup
    assert await jobs.schedule_due() == 7
    assert await jobs.schedule_due() == 0  # next run moved forward

    # Old operational data goes; new stays.
    owner_conn.execute("insert into captcha_spent values ('old', now() - interval '3 days'), ('new', now() + interval '1 hour')")
    owner_conn.execute("""insert into jobs (kind, status, finished_at) values
                          ('noop', 'succeeded', now() - interval '40 days'), ('noop', 'succeeded', now())""")
    ran = await jobs.run_pending()
    assert ran >= 3
    left = [r[0] for r in owner_conn.execute("select challenge from captcha_spent")]
    assert "old" not in left and "new" in left
    assert owner_conn.execute("select count(*) from jobs where finished_at < now() - interval '39 days'").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from audit_log where action = 'retention.cleanup'").fetchone()[0] >= 1
    assert owner_conn.execute("select count(*) from audit_log where action = 'log.purged'").fetchone()[0] >= 1

    r = await admin.patch("/api/admin/retention/policies/jobs", json={"days": 7, "enabled": False})
    assert r.json() == {"key": "jobs", "days": 7, "enabled": False}
    assert (await admin.patch("/api/admin/retention/policies/review_orphans", json={"days": 2})).status_code == 400
    r = await admin.patch("/api/admin/jobs/schedules/maintenance", json={"enabled": False, "interval_seconds": 7200})
    assert r.status_code == 200
    r = await admin.post("/api/admin/jobs/schedules/retention.cleanup/run")
    assert r.json()["job_id"]
    data = (await admin.get("/api/admin/jobs")).json()
    assert "webhook.deliver" in data["handlers"] and data["counts"]["queued"] >= 1
    assert (await admin.post("/api/admin/jobs", json={"kind": "webhook.deliver"})).status_code == 400
    assert (await admin.post("/api/admin/jobs", json={"kind": "noop"})).status_code == 201

    user = make_client()
    await otp_login(user, "72222222")
    assert (await user.get("/api/admin/jobs")).status_code == 403


# ── Webhooks ────────────────────────────────────────────────────────────────

class Receiver:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.answers.pop(0) if self.answers else 200
        return httpx.Response(status, text="ok" if status < 300 else "nope")


@pytest.fixture
def receiver(monkeypatch):
    r = Receiver()
    monkeypatch.setattr(webhooks, "transport", httpx.MockTransport(r))
    return r


async def _admin(make_client, phone):
    admin = make_client()
    auth = await full_user(admin, phone)
    return admin, auth


async def test_webhook_signed_with_optional_auth_header_and_retries(make_client, owner_conn, receiver):
    admin, _ = await _admin(make_client, "81111111")
    # Creating a webhook is a security action: a fresh second factor (full_user just registered a passkey).
    r = await admin.post("/api/admin/webhooks", json={
        "name": "CRM", "url": "https://hooks.example.com/gaer", "events": ["review.feedback.created", "webhook.ping"],
        "auth_header_name": "Authorization", "auth_header_value": "Bearer hemmelig-token-1234"})
    assert r.status_code == 201, r.text
    hook = r.json()
    secret = hook["secret"]
    assert secret.startswith("whsec_") and hook["auth_header_hint"] == "••••1234"
    listed = (await admin.get("/api/admin/webhooks")).json()["items"][0]
    assert "secret" not in listed and "auth_header_enc" not in listed
    r2 = await admin.post("/api/admin/webhooks", json={"name": "Uden auth", "url": "https://other.example.com/x",
                                                       "events": ["*"]})
    hook2 = r2.json()
    assert hook2["auth_header_name"] is None

    # Test button: sent at once, not retried.
    r = await admin.post(f"/api/admin/webhooks/{hook['id']}/test")
    assert r.json()["ok"] is True and r.json()["status"] == 200
    ping = receiver.requests[-1]
    assert ping.headers["authorization"] == "Bearer hemmelig-token-1234"
    assert ping.headers["x-gaer-event"] == "webhook.ping"
    assert webhooks.verify_signature(secret, ping.headers["x-gaer-signature"], ping.content)
    assert not webhooks.verify_signature("whsec_wrong", ping.headers["x-gaer-signature"], ping.content)

    # An event reaches both endpoints (one with, one without an auth header). The first answers 503 once.
    receiver.requests.clear()
    receiver.answers = [503]
    r = await admin.post("/api/review/feedback", json={"path": "/klip", "body": "Knap", "feature_key": "klip"})
    assert r.status_code == 201
    await jobs.run_pending()
    owner_conn.execute("update jobs set run_at = now() where status = 'queued'")
    await jobs.run_pending()
    sent = [q for q in receiver.requests if q.headers["x-gaer-event"] == "review.feedback.created"]
    assert len(sent) == 3
    with_auth = [q for q in sent if "authorization" in q.headers]
    assert with_auth and len(with_auth) < 3  # one endpoint sends the header, the other does not
    by_delivery: dict[str, list] = {}
    for q in sent:
        by_delivery.setdefault(q.headers["x-gaer-delivery"], []).append(q)
    first, retry = next(v for v in by_delivery.values() if len(v) == 2)
    assert (first.headers["x-gaer-attempt"], retry.headers["x-gaer-attempt"]) == ("1", "2")
    body = json.loads(retry.content)
    assert body["event"] == "review.feedback.created" and body["data"]["path"] == "/klip"
    for h in (hook, hook2):
        deliveries = (await admin.get(f"/api/admin/webhooks/{h['id']}/deliveries")).json()
        assert deliveries[0]["status"] == "succeeded" and deliveries[0]["event"] == "review.feedback.created"

    # Removing the auth header, and auditing never includes the value.
    r = await admin.patch(f"/api/admin/webhooks/{hook['id']}", json={"auth_header_value": ""})
    assert r.json()["auth_header_name"] is None
    logged = json.dumps([r for r in owner_conn.execute("select before, after from audit_log where table_name = 'webhooks'")])
    assert "hemmelig" not in logged and secret not in logged

    new_secret = (await admin.post(f"/api/admin/webhooks/{hook['id']}/rotate-secret")).json()["secret"]
    assert new_secret != secret


async def test_webhook_gives_up_and_is_disabled(make_client, owner_conn, receiver, monkeypatch):
    admin, _ = await _admin(make_client, "82222222")
    hook = (await admin.post("/api/admin/webhooks", json={
        "name": "Død", "url": "https://dead.example.com/", "events": ["user.created"], "max_attempts": 2})).json()
    monkeypatch.setattr(webhooks, "FAILURES_BEFORE_DISABLE", 1)
    receiver.answers = [500, 500]
    await otp_login(make_client(), "82222223")  # user.created
    await jobs.run_pending()
    owner_conn.execute("update jobs set run_at = now() where status = 'queued'")
    await jobs.run_pending()
    d = (await admin.get(f"/api/admin/webhooks/{hook['id']}/deliveries")).json()[0]
    assert d["status"] == "dead" and d["attempts"] == 2 and d["last_status"] == 500
    h = next(i for i in (await admin.get("/api/admin/webhooks")).json()["items"] if i["id"] == hook["id"])
    assert h["active"] is False and "i træk" in h["disabled_reason"]
    assert owner_conn.execute("select count(*) from audit_log where action = 'webhook.disabled'").fetchone()[0] == 1

    # Switching it on again resets the counter; redelivery sends the same delivery id again.
    await admin.patch(f"/api/admin/webhooks/{hook['id']}", json={"active": True})
    receiver.requests.clear()
    assert (await admin.post(f"/api/admin/webhooks/deliveries/{d['id']}/redeliver")).status_code == 200
    await jobs.run_pending()
    assert receiver.requests[-1].headers["x-gaer-delivery"] == d["id"]
    d = (await admin.get(f"/api/admin/webhooks/{hook['id']}/deliveries")).json()[0]
    assert d["status"] == "succeeded"


def test_unsafe_urls_are_refused(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE", "0")
    config.reload()
    try:
        for url in ("http://example.com/", "https://127.0.0.1/", "https://10.0.0.5/x", "https://[::1]/",
                    "https://169.254.169.254/latest", "https://localhost/", "https://user:pw@example.com/",
                    "ftp://example.com/"):
            with pytest.raises(webhooks.UnsafeUrl):
                webhooks.check_url(url)
        assert webhooks.check_url("https://hooks.example.com/x") == "https://hooks.example.com/x"
    finally:
        monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE", "1")
        config.reload()
