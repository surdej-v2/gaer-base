"""Mail module (specs/mail): /api/mail for the user and shared-mailbox managers, /api/admin/mail for operators,
the jobs (provision, retention, sync, backup, archive export) and the Lettermint handler. Runs on the in-memory
mail server (MAILBOX_BACKEND=memory)."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app import config, jobs
from app.mailbox import backend as mb_backend
from app.mailbox import dns as mail_dns
from app.mailbox import tasks
from app.storage import get_storage
from tests.helpers import SoftAuthenticator, register_passkey, stepup
from tests.test_email_login import email_login

DOMAIN = "firma.dk"


@pytest.fixture
def mailserver(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, mailbox_backend="memory",
                                                                mail_hostname="mail.firma.dk"))
    mem = mb_backend.memory()
    mem.reset()
    yield mem
    mem.reset()


async def _setup(make_client, *emails):
    """Admin (first user) + users, a domain, and returns (admin, auth, clients, ids, domain_id)."""
    admin = make_client()
    await email_login(admin, "admin@firma.dk")
    auth = SoftAuthenticator()
    await register_passkey(admin, auth)
    clients, ids = [], []
    for e in emails:
        c = make_client()
        await email_login(c, e)
        clients.append(c)
        ids.append((await c.get("/api/auth/me")).json()["user"]["id"])
    r = await admin.post("/api/admin/mail/domains", json={"name": DOMAIN})
    assert r.status_code == 200, r.text
    return admin, auth, clients, ids, r.json()["id"]


async def _provision(admin, user_id: int, domain_id: int, local: str | None = None) -> dict:
    r = await admin.post(f"/api/admin/mail/users/{user_id}/mailbox",
                         json={"domain_id": domain_id, **({"local_part": local} if local else {})})
    assert r.status_code == 200, r.text
    return r.json()


async def _as_stepup(client, auth, method: str, url: str, **kw):
    r = await client.request(method, url, **kw)
    if r.status_code == 403 and r.headers.get("x-gaer-error") == "stepup_required":
        assert (await stepup(client, auth)).status_code == 200
        r = await client.request(method, url, **kw)
    return r


def _actions(owner_conn) -> list[str]:
    return [r[0] for r in owner_conn.execute("select action from audit_log where action like 'mail.%' order by id")]


async def test_off_by_default(client):
    await email_login(client, "admin@firma.dk")
    assert (await client.get("/api/mail/status")).json() == {"enabled": False}
    assert (await client.get("/api/mail/users/me")).status_code == 503
    assert (await client.get("/api/admin/mail/overview")).json()["enabled"] is False
    assert (await client.get("/api/admin/mail/domains")).status_code == 503


async def test_provision_and_self_service(make_client, mailserver, owner_conn):
    admin, auth, (anne, bo), (anne_id, bo_id), domain_id = await _setup(make_client, "anne@example.com", "bo@example.com")
    owner_conn.execute("update users set display_name = 'Anne Ærø' where id = %s", (anne_id,))

    # Before provisioning: no mailbox, other ids are not found (not forbidden).
    assert (await anne.get("/api/mail/status")).json() == {"enabled": True, "has_mailbox": False, "status": None}
    assert (await anne.get("/api/mail/users/me")).json()["mailbox"] is None
    assert (await anne.get(f"/api/mail/users/{bo_id}")).status_code == 404

    data = await _provision(admin, anne_id, domain_id)
    assert data["mailbox"]["address"] == "anne.aeroe@firma.dk" and data["mailbox"]["status"] == "active"
    assert mailserver.principals["anne.aeroe@firma.dk"]["emails"] == ["anne.aeroe@firma.dk"]
    me = (await anne.get("/api/mail/users/me")).json()
    assert me["mailbox"]["address"] == "anne.aeroe@firma.dk" and me["retention"]["days"] == 30
    assert [i["from_address"] for i in me["identities"]] == ["anne.aeroe@firma.dk"]
    assert me["client"]["imap"] == {"host": "mail.firma.dk", "port": 993, "security": "SSL/TLS"}
    assert (await anne.get(f"/api/mail/users/{anne_id}")).json()["mailbox"]["id"] == me["mailbox"]["id"]
    # Default calendar and address book, in the app and in Stalwart.
    cals = (await anne.get("/api/mail/users/me/calendars")).json()
    books = (await anne.get("/api/mail/users/me/address-books")).json()
    assert [(c["name"], c["is_default"], c["role"]) for c in cals] == [("Kalender", True, "owner")]
    assert [b["name"] for b in books] == ["Kontakter"]
    assert ("anne.aeroe@firma.dk", "calendar", "default") in mailserver.collections
    # A second mailbox for the same user is refused; so is a taken address.
    r = await admin.post(f"/api/admin/mail/users/{anne_id}/mailbox", json={"domain_id": domain_id})
    assert r.status_code == 409
    r = await admin.post(f"/api/admin/mail/users/{bo_id}/mailbox", json={"domain_id": domain_id,
                                                                         "local_part": "anne.aeroe"})
    assert r.status_code == 409

    # App passwords: the secret is returned once and stored only in Stalwart.
    r = await anne.post("/api/mail/users/me/app-passwords", json={"name": "Thunderbird"})
    assert r.status_code == 200, r.text
    pw = r.json()
    assert pw["username"] == "anne.aeroe@firma.dk" and len(pw["password"]) == 29
    secrets_ = mailserver.principals["anne.aeroe@firma.dk"]["secrets"]
    assert len(secrets_) == 1 and secrets_[0].endswith("$" + pw["password"])
    listed = (await anne.get("/api/mail/users/me/app-passwords")).json()
    assert [p["name"] for p in listed] == ["Thunderbird"] and "password" not in listed[0]
    assert pw["password"] not in str(owner_conn.execute("select * from audit_log").fetchall())
    assert (await bo.delete(f"/api/mail/users/{anne_id}/app-passwords/{pw['id']}")).status_code == 404
    assert (await anne.delete(f"/api/mail/users/me/app-passwords/{pw['id']}")).status_code == 200
    assert mailserver.principals["anne.aeroe@firma.dk"]["secrets"] == []

    # Identity: display name and signature go to Stalwart; the signature is not logged.
    identity = me["identities"][0]
    r = await anne.patch(f"/api/mail/users/me/identities/{identity['id']}",
                         json={"display_name": "Anne Æ.", "signature": "Hilsen Anne\nFirma"})
    assert r.status_code == 200 and r.json()["display_name"] == "Anne Æ."
    assert mailserver.identities[("anne.aeroe@firma.dk", "anne.aeroe@firma.dk")]["signature"] == "Hilsen Anne\nFirma"
    assert "Hilsen Anne" not in str(owner_conn.execute("select after from audit_log").fetchall())

    # Alias: the user asks, the admin approves; the alias reaches Stalwart and becomes an identity.
    r = await anne.post("/api/mail/users/me/alias-requests", json={"address": "Salg@firma.dk", "note": "Til salg"})
    assert r.status_code == 200, r.text
    assert (await anne.post("/api/mail/users/me/alias-requests", json={"address": "x@andet.dk"})).status_code == 400
    req = (await admin.get("/api/admin/mail/alias-requests")).json()
    assert [(x["address"], x["email"]) for x in req] == [("salg@firma.dk", "anne@example.com")]
    assert (await admin.post(f"/api/admin/mail/alias-requests/{req[0]['id']}", json={"approve": True})).status_code == 200
    assert mailserver.principals["anne.aeroe@firma.dk"]["emails"] == ["anne.aeroe@firma.dk", "salg@firma.dk"]
    me = (await anne.get("/api/mail/users/me")).json()
    assert [a["address"] for a in me["aliases"]] == ["salg@firma.dk"]
    assert sorted(i["from_address"] for i in me["identities"]) == ["anne.aeroe@firma.dk", "salg@firma.dk"]

    # Calendars: create, rename, share with a colleague (who needs a mailbox first).
    r = await anne.post("/api/mail/users/me/calendars", json={"name": "Projekt", "colour": "#2563eb"})
    assert r.status_code == 200, r.text
    cal = r.json()
    r = await anne.patch(f"/api/mail/users/me/calendars/{cal['id']}", json={"name": "Projekt X", "freebusy": False})
    assert r.status_code == 200 and r.json()["name"] == "Projekt X"
    assert mailserver.collections[("anne.aeroe@firma.dk", "calendar", cal["slug"])].name == "Projekt X"
    assert mailserver.collections[("anne.aeroe@firma.dk", "calendar", cal["slug"])].freebusy is False
    r = await anne.post(f"/api/mail/collections/{cal['id']}/shares", json={"email": "bo@example.com", "role": "editor"})
    assert r.status_code == 409  # Bo has no mailbox yet
    await _provision(admin, bo_id, domain_id, "bo")
    r = await anne.post(f"/api/mail/collections/{cal['id']}/shares", json={"email": "bo@example.com", "role": "editor"})
    assert r.status_code == 200 and [(s["email"], s["role"]) for s in r.json()] == [("bo@example.com", "editor")]
    assert mailserver.collections[("anne.aeroe@firma.dk", "calendar", cal["slug"])].access == {"bo@firma.dk": "editor"}
    bo_cals = (await bo.get("/api/mail/users/me/calendars")).json()
    assert {(c["name"], c["role"]) for c in bo_cals} == {("Kalender", "owner"), ("Projekt X", "editor")}
    # An editor may not rename, share or delete; may leave.
    assert (await bo.patch(f"/api/mail/users/me/calendars/{cal['id']}", json={"name": "Bo"})).status_code == 403
    assert (await bo.delete(f"/api/mail/users/me/calendars/{cal['id']}")).status_code == 403
    assert (await bo.delete(f"/api/mail/collections/{cal['id']}/shares/{bo_id}")).status_code == 200
    assert mailserver.collections[("anne.aeroe@firma.dk", "calendar", cal["slug"])].access == {}
    # The default calendar cannot be deleted; an own one can.
    default = next(c for c in cals if c["is_default"])
    assert (await anne.delete(f"/api/mail/users/me/calendars/{default['id']}")).status_code == 409
    assert (await anne.delete(f"/api/mail/users/me/calendars/{cal['id']}")).status_code == 200
    assert ("anne.aeroe@firma.dk", "calendar", cal["slug"]) not in mailserver.collections
    # Someone else's calendar id: 404.
    assert (await bo.get(f"/api/mail/collections/{default['id']}/export")).status_code == 404

    # Address book: import vCards, export them.
    book = books[0]
    vcf = (b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Karl\r\nUID:karl-1\r\nEND:VCARD\r\n"
           b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Lise\r\nEND:VCARD\r\n")
    r = await anne.post(f"/api/mail/users/me/address-books/{book['id']}/import",
                        files={"file": ("kontakter.vcf", vcf, "text/vcard")})
    assert r.status_code == 200 and r.json() == {"imported": 2, "entries": 2}
    r = await anne.get(f"/api/mail/collections/{book['id']}/export")
    assert r.status_code == 200 and r.text.count("BEGIN:VCARD") == 2 and "FN:Karl" in r.text

    # Working hours and free/busy.
    r = await anne.put("/api/mail/users/me/availability",
                       json={"publish": False, "working_hours": {"days": [1, 2, 3, 4], "start": "09:00", "end": "15:00"}})
    assert r.status_code == 200, r.text
    assert mailserver.availability["anne.aeroe@firma.dk"]["publish"] is False
    r = await anne.put("/api/mail/users/me/availability",
                       json={"publish": True, "working_hours": {"days": [1], "start": "15:00", "end": "09:00"}})
    assert r.status_code == 400

    # Apple profile: mail, calendar and contacts, no password.
    r = await anne.get("/api/mail/users/me/client-profile")
    assert r.status_code == 200 and b"com.apple.caldav.account" in r.content
    assert b"<key>IncomingPassword</key>" not in r.content and b"<key>CalDAVPassword</key>" not in r.content

    actions = _actions(owner_conn)
    for a in ("mail.domain.create", "mail.mailbox.create", "mail.mailbox.provisioned", "mail.app_password.create",
              "mail.app_password.revoke", "mail.identity.update", "mail.alias.request", "mail.alias.create",
              "mail.collection.create", "mail.collection.update", "mail.collection.share", "mail.collection.delete",
              "mail.contacts.import", "mail.availability.update"):
        assert a in actions, a


async def test_shared_mailbox_roles(make_client, mailserver, owner_conn):
    admin, auth, (mia, ole, pia), (mia_id, ole_id, pia_id), domain_id = await _setup(
        make_client, "mia@example.com", "ole@example.com", "pia@example.com")
    for uid, local in ((mia_id, "mia"), (ole_id, "ole"), (pia_id, "pia")):
        await _provision(admin, uid, domain_id, local)
    r = await admin.post("/api/admin/mail/shared", json={"domain_id": domain_id, "local_part": "support",
                                                         "display_name": "Support", "managers": [mia_id]})
    assert r.status_code == 200, r.text
    shared = r.json()
    assert shared["status"] == "active" and mailserver.principals["support@firma.dk"]["type"] == "group"
    assert mailserver.principals["support@firma.dk"]["members"] == {"mia@firma.dk": "manager"}

    # The manager adds a reader and a sender; members see their role, only managers see the members.
    r = await mia.post(f"/api/mail/shared/{shared['id']}/members", json={"email": "ole@example.com", "role": "reader"})
    assert r.status_code == 200, r.text
    r = await mia.post(f"/api/mail/shared/{shared['id']}/members", json={"email": "pia@example.com", "role": "sender"})
    assert r.status_code == 200
    assert mailserver.principals["support@firma.dk"]["members"] == {
        "mia@firma.dk": "manager", "ole@firma.dk": "reader", "pia@firma.dk": "sender"}
    ole_view = (await ole.get(f"/api/mail/shared/{shared['id']}")).json()
    assert ole_view["role"] == "reader" and ole_view["members"] is None
    assert (await ole.post(f"/api/mail/shared/{shared['id']}/members",
                           json={"email": "pia@example.com", "role": "manager"})).status_code == 403
    # Only senders and managers get the shared address as an identity.
    pia_ids = [i["from_address"] for i in (await pia.get("/api/mail/users/me")).json()["identities"]]
    ole_ids = [i["from_address"] for i in (await ole.get("/api/mail/users/me")).json()["identities"]]
    assert "support@firma.dk" in pia_ids and "support@firma.dk" not in ole_ids
    assert [(s["address"], s["role"]) for s in (await ole.get("/api/mail/users/me")).json()["shared"]] == [
        ("support@firma.dk", "reader")]
    # Non-members: 404.
    outsider = make_client()
    await email_login(outsider, "x@example.com")
    assert (await outsider.get(f"/api/mail/shared/{shared['id']}")).status_code == 404
    # The last manager cannot step down as manager; a member can leave.
    assert (await mia.put(f"/api/mail/shared/{shared['id']}/members/{mia_id}", json={"role": "reader"})).status_code == 409
    assert (await ole.delete(f"/api/mail/shared/{shared['id']}/members/{ole_id}")).status_code == 200
    assert "ole@firma.dk" not in mailserver.principals["support@firma.dk"]["members"]
    # Pia downgraded to reader loses the identity.
    r = await admin.put(f"/api/admin/mail/shared/{shared['id']}/members/{pia_id}", json={"role": "reader"})
    assert r.status_code == 200
    assert "support@firma.dk" not in [i["from_address"] for i in (await pia.get("/api/mail/users/me")).json()["identities"]]
    # Deleting needs a reason and a fresh second factor.
    r = await _as_stepup(admin, auth, "DELETE", f"/api/admin/mail/shared/{shared['id']}?reason=kort")
    assert r.status_code == 400
    r = await admin.delete(f"/api/admin/mail/shared/{shared['id']}?reason=Teamet er lukket ned")
    assert r.status_code == 200, r.text
    assert "support@firma.dk" not in mailserver.principals
    assert (await mia.get(f"/api/mail/shared/{shared['id']}")).status_code == 404
    assert "mail.shared.delete" in _actions(owner_conn)


async def test_retention_and_archive(make_client, mailserver, owner_conn):
    admin, auth, (anne,), (anne_id,), domain_id = await _setup(make_client, "anne@example.com")
    await _provision(admin, anne_id, domain_id, "anne")
    now = datetime.now(timezone.utc)
    mailserver.add_message("anne@firma.dk", now - timedelta(days=40), "inbox", b"Subject: gammel\r\n\r\nx")
    mailserver.add_message("anne@firma.dk", now - timedelta(days=10), "trash")
    mailserver.add_message("anne@firma.dk", now - timedelta(days=1), "inbox")
    # The policy can never be longer than 30 days.
    assert (await admin.put("/api/admin/mail/retention", json={"days": 31})).status_code == 422

    # Dry run counts without deleting.
    r = await admin.post("/api/admin/mail/retention/run?dry_run=true")
    assert r.status_code == 200 and r.json()["expired"] == 1 and r.json()["dry_run"] is True
    assert len(mailserver.messages["anne@firma.dk"]) == 3

    # A domain rule of 7 days and archive export on: the 10-day and 40-day messages expire, archived first.
    assert (await admin.put(f"/api/admin/mail/retention/domains/{domain_id}",
                            json={"days": 7, "applies_to": "all"})).status_code == 200
    r = await admin.put("/api/admin/mail/retention", json={"days": 30, "archive_export": True})
    assert r.status_code == 200 and r.json()["archive_export"] is True
    r = await admin.post("/api/admin/mail/retention/run")
    assert r.status_code == 200 and r.json()["queued"] is True
    await jobs.run_pending()
    assert len(mailserver.messages["anne@firma.dk"]) == 1
    archived = [k for k in get_storage().objects if k.startswith("mail-archive/anne@firma.dk/")]
    assert len(archived) == 2
    run = (await admin.get("/api/admin/mail/retention")).json()["runs"][0]
    assert (run["expired"], run["archived"], run["dry_run"], run["status"]) == (2, 2, False, "succeeded")
    assert owner_conn.execute("select type, count from mail_events").fetchall() == [("expired", 2)]
    # The user sees the last expiry run; with archive export on they can ask for their mail as a zip.
    assert (await anne.get("/api/mail/users/me/activity")).json()["last_expiry_run"]["expired"] == 2
    r = await anne.post("/api/mail/users/me/archive-export")
    assert r.status_code == 200, r.text
    await jobs.run_pending()
    files = owner_conn.execute("select folder, filename, content_type from files where user_id = %s", (anne_id,)).fetchall()
    assert len(files) == 1 and files[0][0] == "/Mail-arkiv/" and files[0][1].startswith("mail-")
    assert files[0][2] == "application/zip"
    # Calendars and contacts are never touched by retention.
    assert ("anne@firma.dk", "calendar", "default") in mailserver.collections


async def test_lettermint_events_and_activity(make_client, mailserver, owner_conn):
    admin, auth, (anne,), (anne_id,), domain_id = await _setup(make_client, "anne@example.com")
    await _provision(admin, anne_id, domain_id, "anne")
    endpoint = {"id": 7}
    for i, (name, mid) in enumerate([("message.sent", "m1"), ("message.delivered", "m1"),
                                     ("message.sent", "m2"), ("message.hard_bounced", "m2")]):
        event = {"id": f"in-{i}", "event": None, "body_json": {
            "id": f"evt-{i}", "event": name, "data": {"message_id": mid, "from": "Anne <anne@firma.dk>",
                                                      "to": ["kunde@kunde.dk"]}}}
        assert (await tasks.on_lettermint(event, endpoint))["stored"] == 1
    # The same event again is stored once.
    assert (await tasks.on_lettermint({"id": "x", "event": None, "body_json": {
        "id": "evt-0", "event": "message.sent", "data": {"message_id": "m1", "from": "anne@firma.dk"}}}, endpoint))["stored"] == 0
    rows = owner_conn.execute("select type, recipient_domain from mail_events order by id").fetchall()
    assert rows[0] == ("sent", "kunde.dk") and len(rows) == 4
    assert "kunde@kunde.dk" not in str(owner_conn.execute("select * from mail_events").fetchall())
    items = (await anne.get("/api/mail/users/me/activity")).json()["items"]
    assert {(i["message_id"], i["status"]) for i in items} == {("m1", "delivered"), ("m2", "bounced")}
    sending = (await admin.get("/api/admin/mail/sending")).json()
    assert sending["rates_24h"]["bounced"] == 1
    overview = (await admin.get("/api/admin/mail/overview")).json()
    assert any(w["key"] == "bounce" for w in overview["warnings"])


async def test_offboarding_convert(make_client, mailserver, owner_conn):
    admin, auth, (leaver, boss), (leaver_id, boss_id), domain_id = await _setup(
        make_client, "leaver@example.com", "boss@example.com")
    await _provision(admin, leaver_id, domain_id, "leaver")
    await _provision(admin, boss_id, domain_id, "boss")
    shared = (await admin.post("/api/admin/mail/shared", json={"domain_id": domain_id, "local_part": "team",
                                                               "display_name": "Team", "managers": [boss_id]})).json()
    book = (await leaver.get("/api/mail/users/me/address-books")).json()[0]
    await leaver.post(f"/api/mail/users/me/address-books/{book['id']}/import",
                      files={"file": ("a.vcf", b"BEGIN:VCARD\r\nFN:A\r\nUID:a\r\nEND:VCARD\r\n", "text/vcard")})
    await leaver.post("/api/mail/users/me/app-passwords", json={"name": "Telefon"})

    body = {"action": "convert", "shared_mailbox_id": shared["id"], "collections": "transfer", "managers": [boss_id],
            "reason": "Medarbejderen er stoppet"}
    r = await _as_stepup(admin, auth, "POST", f"/api/admin/mail/users/{leaver_id}/offboard", json={**body, "reason": "x"})
    assert r.status_code == 400
    r = await admin.post(f"/api/admin/mail/users/{leaver_id}/offboard", json=body)
    assert r.status_code == 200, r.text
    assert {m["type"] for m in r.json()["moved"]} == {"calendar", "address_book"}
    # The address now lands in the team mailbox; the personal principal is gone; the contacts survived.
    assert "leaver@firma.dk" not in mailserver.principals
    assert mailserver.principals["team@firma.dk"]["emails"] == ["team@firma.dk", "leaver@firma.dk"]
    boss_books = (await boss.get("/api/mail/users/me/address-books")).json()
    moved = next(b for b in boss_books if b["role"] == "manager")
    assert moved["kind"] == "shared" and moved["entry_count"] == 1
    assert (await leaver.get("/api/mail/users/me")).json()["mailbox"] is None
    assert owner_conn.execute("select count(*) from mail_app_passwords where revoked_at is null").fetchone()[0] == 0
    assert "mail.mailbox.delete" in _actions(owner_conn)
    # The address can be reused only as the alias it now is.
    r = await admin.post(f"/api/admin/mail/users/{boss_id}/aliases", json={"address": "leaver@firma.dk"})
    assert r.status_code == 409


async def test_suspend_needs_reason_and_stepup(make_client, mailserver, owner_conn):
    admin, auth, (anne,), (anne_id,), domain_id = await _setup(make_client, "anne@example.com")
    await _provision(admin, anne_id, domain_id, "anne")
    r = await admin.patch(f"/api/admin/mail/users/{anne_id}/mailbox", json={"quota_mb": 2048})
    assert r.status_code == 200 and mailserver.principals["anne@firma.dk"]["quota_mb"] == 2048
    r = await _as_stepup(admin, auth, "PATCH", f"/api/admin/mail/users/{anne_id}/mailbox", json={"status": "suspended"})
    assert r.status_code == 400
    r = await admin.patch(f"/api/admin/mail/users/{anne_id}/mailbox",
                          json={"status": "suspended", "reason": "Mistanke om misbrug af kontoen"})
    assert r.status_code == 200, r.text
    assert mailserver.principals["anne@firma.dk"]["enabled"] is False
    assert (await anne.post("/api/mail/users/me/app-passwords", json={"name": "x"})).status_code == 409
    detail = (await admin.get(f"/api/admin/mail/users/{anne_id}")).json()
    assert detail["mailbox"]["status"] == "suspended"
    assert detail["audit"][0]["action"] == "mail.mailbox.suspend"
    # The module audit can be filtered and exported.
    r = await admin.get("/api/admin/mail/audit?action=mailbox")
    assert {i["action"] for i in r.json()["items"]} >= {"mail.mailbox.suspend", "mail.mailbox.create"}
    r = await admin.get("/api/admin/mail/audit?format=csv")
    assert r.status_code == 200 and "mail.mailbox.suspend" in r.text and r.headers["content-type"].startswith("text/csv")


async def test_backend_failure_rolls_back(make_client, mailserver, owner_conn):
    admin, auth, (anne,), (anne_id,), domain_id = await _setup(make_client, "anne@example.com")
    # Stalwart down while provisioning: the mailbox waits and the job retries.
    mailserver.fail = "ensure_account"
    data = await _provision(admin, anne_id, domain_id, "anne")
    assert data["mailbox"]["status"] == "provisioning"
    assert (await anne.get("/api/mail/users/me")).json()["mailbox"]["status"] == "provisioning"
    await jobs.run_pending()
    assert (await anne.get("/api/mail/users/me")).json()["mailbox"]["status"] == "active"
    # A refused change leaves no row behind.
    mailserver.fail = "ensure_collection"
    r = await anne.post("/api/mail/users/me/calendars", json={"name": "Nej"})
    assert r.status_code == 502
    assert owner_conn.execute("select count(*) from mail_collections where name = 'Nej'").fetchone()[0] == 0


async def test_bulk_sync_backup_and_domains(make_client, mailserver, owner_conn, monkeypatch):
    admin, auth, _, ids, domain_id = await _setup(make_client, "a@example.com", "b@example.com")
    r = await admin.post("/api/admin/mail/mailboxes/bulk", json={"user_ids": ids + [ids[0]], "domain_id": domain_id})
    assert r.status_code == 200 and len(r.json()["created"]) == 2
    assert (await admin.get("/api/admin/mail/mailboxes?status=provisioning")).json()["total"] == 2
    await jobs.run_pending()
    assert (await admin.get("/api/admin/mail/mailboxes?status=active")).json()["total"] == 2
    # Drift: a principal removed behind the app's back, and one that exists only in Stalwart.
    address = (await admin.get("/api/admin/mail/mailboxes")).json()["items"][0]["address"]
    del mailserver.principals[address]
    mailserver.principals["stray@firma.dk"] = {"type": "individual", "emails": []}
    await tasks.sync_job({}, {})
    health = (await admin.get("/api/admin/mail/health")).json()
    assert [d["address"] for d in health["drift"]] == [address]
    assert {d["address"] for d in health["last_sync"]["drift"]} == {address, "stray@firma.dk"}
    # Backup: every calendar and address book to the backup prefix.
    result = await tasks.backup_job({}, {})
    assert result["calendars"] == 2 and result["address_books"] == 2 and result["failures"] == 0
    assert any(k.startswith("mail-backup/") and k.endswith(".ics") for k in get_storage().objects)

    # Domains: live DNS check (fake resolver).
    records = {("firma.dk", "MX"): ["10 mail.firma.dk."], ("firma.dk", "TXT"): ["v=spf1 include:spf.lettermint.co -all"],
               ("lm._domainkey.firma.dk", "CNAME"): ["lm.dkim.lettermint.co."],
               ("_dmarc.firma.dk", "TXT"): ["v=DMARC1; p=quarantine"]}

    async def fake(name, rdtype):
        return records.get((name, rdtype), [])
    monkeypatch.setattr(mail_dns, "resolve", fake)
    r = await admin.post(f"/api/admin/mail/domains/{domain_id}/verify")
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert r.json()["checks"]["caldav"]["ok"] is False
    records[("_dmarc.firma.dk", "TXT")] = ["v=DMARC1; p=none"]
    assert (await admin.post(f"/api/admin/mail/domains/{domain_id}/verify")).json()["status"] == "pending"


async def test_gdpr_and_admin_roles(make_client, mailserver, owner_conn):
    admin, auth, (anne,), (anne_id,), domain_id = await _setup(make_client, "anne@example.com")
    await _provision(admin, anne_id, domain_id, "anne")
    export = (await anne.get("/api/me/export")).content
    import io
    import json
    import zipfile
    data = json.loads(zipfile.ZipFile(io.BytesIO(export)).read("data.json"))
    assert data["mailbox"][0]["address"] == "anne@firma.dk"
    assert {c["name"] for c in data["mail_collections"]} == {"Kalender", "Kontakter"}

    # Erasure removes the rows at once and the principal in Stalwart through a job.
    from app import gdpr
    from app.logs import RequestMeta
    await gdpr.erase_user(anne_id, actor_id=None, meta=RequestMeta(), reason="test")
    assert owner_conn.execute("select count(*) from mailboxes").fetchone()[0] == 0
    assert "anne@firma.dk" in mailserver.principals
    await jobs.run_pending()
    assert "anne@firma.dk" not in mailserver.principals

    # Admin roles: Drift opens /admin/mail, Support does not.
    from app import admin_roles
    assert admin_roles.allows(["drift"], "POST", "/api/admin/mail/domains")
    assert "/mail" in admin_roles.sections(["drift"])
    assert not admin_roles.allows(["support"], "GET", "/api/admin/mail/overview")
    # Ordinary users never reach the admin API.
    other = make_client()
    await email_login(other, "o@example.com")
    assert (await other.get("/api/admin/mail/overview")).status_code == 403


async def test_stalwart_client_auth_and_principal(monkeypatch):
    """The Stalwart client logs in with "<principal>:<secret>" as HTTP Basic (deploy/mail/bootstrap.sh) or a bearer token."""
    import base64

    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/principal/anne@firma.dk":
            return httpx.Response(200, json={"data": {"type": "individual", "emails": ["anne@firma.dk"],
                                                      "quota": 2 * 1_048_576, "usedQuota": 5,
                                                      "disabledPermissions": ["authenticate"]}})
        return httpx.Response(404, json={"error": "notFound"})

    monkeypatch.setattr(mb_backend.StalwartBackend, "transport", httpx.MockTransport(handler))
    for key, expected in (("gaer-api:hemmelig", "Basic " + base64.b64encode(b"gaer-api:hemmelig").decode()),
                          ("token123", "Bearer token123")):
        monkeypatch.setattr(config, "settings", dataclasses.replace(
            config.settings, mailbox_backend="stalwart", stalwart_url="https://mail.firma.dk:8443", stalwart_api_key=key))
        be = mb_backend.get_backend()
        info = await be.principal("anne@firma.dk")
        assert info["emails"] == ["anne@firma.dk"] and info["quota_mb"] == 2 and info["enabled"] is False
        assert await be.principal("ingen@firma.dk") is None
        assert seen[-1][2] == expected
