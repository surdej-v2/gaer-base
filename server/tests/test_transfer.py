"""/admin/data/transfer: export and import of data (app/transfer.py)."""

import hashlib
import io
import json
import zipfile

import pytest

from app import jobs, transfer
from app.storage import get_storage
from tests.helpers import full_user, otp_login, stepup

BASE = "/api/admin/transfer"
CONFIG_AREAS = ["web", "groups", "settings", "meta", "webhooks", "clips", "users"]


@pytest.fixture(autouse=True)
def clean_transfer_tables(owner_conn):
    """Tables this file fills that the shared clean-up leaves alone."""
    owner_conn.execute("truncate web_pages, groups, webhooks, data_transfers restart identity cascade")


async def admin(make_client):
    c = make_client()
    key = await full_user(c, "11111111")
    r = await stepup(c, key)
    assert r.status_code == 200, r.text
    return c


async def export(c, **options) -> dict:
    r = await c.post(f"{BASE}/exports", json={"areas": CONFIG_AREAS, "personal": "with", **options})
    assert r.status_code == 200, r.text
    await jobs.run_pending()
    row = (await c.get(f"{BASE}/{r.json()['id']}")).json()
    assert row["status"] == "ready", row
    return row


def package_bytes(owner_conn, transfer_id: int) -> bytes:
    key = owner_conn.execute("select storage_key from data_transfers where id = %s", (transfer_id,)).fetchone()[0]
    return get_storage().objects[key][0]


async def upload(c, data: bytes, password: str = "") -> dict:
    r = await c.post(f"{BASE}/imports", files={"file": ("pakke.zip", data, "application/zip")},
                     data={"password": password})
    assert r.status_code == 200, r.text
    return r.json()


async def dry_run(c, tid: int, **options) -> dict:
    r = await c.post(f"{BASE}/{tid}/dry-run", json=options)
    assert r.status_code == 200, r.text
    await jobs.run_pending()
    row = (await c.get(f"{BASE}/{tid}")).json()
    assert row["status"] == "dry_ready", row
    return row


def totals(report: dict, table: str) -> dict:
    return next(t for t in report["tables"] if t["table"] == table)


def seed(owner_conn) -> dict:
    owner_conn.execute("""insert into web_pages (slug, lang, title, description, body, published)
                          values ('priser', 'da', 'Priser', '', 'Vores priser', true)""")
    gid = owner_conn.execute("""insert into groups (slug, name, description, notify_email)
                                values ('drift', 'Drift', 'Driftsfolk', 'drift@example.com') returning id""").fetchone()[0]
    uid = owner_conn.execute("select id from users where phone = '+4522222222'").fetchone()[0]
    owner_conn.execute("insert into group_members (group_id, user_id, role) values (%s, %s, 'member')", (gid, uid))
    owner_conn.execute("insert into clips (user_id, source, title, body) values (%s, 'manual', 'Husk', 'Mælk')", (uid,))
    owner_conn.execute("""insert into settings (key, value) values ('app_name', '"Kundens app"'),
                          ('retention_last_run', '"2026-01-01"')""")
    did = owner_conn.execute("""insert into meta_dimensions (slug, label) values ('org', 'Organisation')
                                returning id""").fetchone()[0]
    root = owner_conn.execute("""insert into meta_nodes (dimension_id, path, slug, label, depth)
                                 values (%s, '/org/drift', 'drift', 'Drift', 1) returning id""", (did,)).fetchone()[0]
    owner_conn.execute("""insert into meta_nodes (dimension_id, parent_id, path, slug, label, depth)
                          values (%s, %s, '/org/drift/support', 'support', 'Support', 2)""", (did, root))
    owner_conn.execute("""insert into webhooks (name, url, events, secret_enc, active)
                          values ('CRM', 'https://crm.example.com/hook', '{user.created}', '\\x00', true)""")
    return {"group": gid, "user": uid}


# ── Export ──────────────────────────────────────────────────────────────────

async def test_areas_and_only_full_admins(make_client, owner_conn):
    c = await admin(make_client)
    r = await c.get(f"{BASE}/areas")
    assert r.status_code == 200, r.text
    body = r.json()
    keys = [a["key"] for a in body["areas"]]
    assert {"users", "roles", "web", "settings", "audit_log"} <= set(keys)
    assert next(a for a in body["areas"] if a["key"] == "users")["rows"] == 1
    assert next(a for a in body["areas"] if a["key"] == "audit_log")["importable"] is False
    assert "passkeys og sikkerhedsnøgler" in body["never"]

    other = make_client()
    await full_user(other, "33333333")
    owner_conn.execute("update users set role = 'admin', admin_roles = '{data}' where phone = '+4533333333'")
    assert (await other.get(f"{BASE}/areas")).status_code == 403


async def test_export_needs_stepup(make_client):
    c = make_client()
    await full_user(c, "11111111")
    owner = make_client()
    # A fresh session without a recent second factor.
    await otp_login(owner, "11111111")
    r = await owner.post(f"{BASE}/exports", json={"areas": ["web"]})
    assert r.status_code == 403 and r.headers["x-gaer-error"] in ("stepup_required", "mfa_required")


async def test_export_writes_package_without_secrets(make_client, owner_conn):
    c = await admin(make_client)
    await otp_login(make_client(), "22222222")
    seed(owner_conn)
    row = await export(c)
    assert row["manifest"]["areas"] == CONFIG_AREAS and row["manifest"]["personal"] == "with"
    zf = zipfile.ZipFile(io.BytesIO(package_bytes(owner_conn, row["id"])))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["tables"]["web_pages"]["rows"] == 1
    hooks = json.loads(zf.read("data/webhooks.json"))
    assert hooks[0]["name"] == "CRM" and "secret_enc" not in hooks[0]
    settings = {s["key"] for s in json.loads(zf.read("data/settings.json"))}
    assert settings == {"app_name"}                       # values the app writes itself stay home
    groups = json.loads(zf.read("data/groups.json"))
    assert groups[0]["notify_email"] == "drift@example.com"
    audit = owner_conn.execute("select action from audit_log where table_name = 'data_transfers' order by id").fetchall()
    assert [a[0] for a in audit] == ["transfer.export_requested", "transfer.exported"]


async def test_pseudonymised_and_without_personal_data(make_client, owner_conn):
    c = await admin(make_client)
    await otp_login(make_client(), "22222222")
    seed(owner_conn)
    row = await export(c, personal="pseudo")
    zf = zipfile.ZipFile(io.BytesIO(package_bytes(owner_conn, row["id"])))
    users = json.loads(zf.read("data/users.json"))
    assert {u["phone"] for u in users} == {None}
    assert all(u["email"].endswith("@eksport.invalid") for u in users)
    assert json.loads(zf.read("data/groups.json"))[0]["notify_email"] is None
    assert "role" not in users[0]                          # Admin-roller was not chosen

    row = await export(c, personal="without")
    assert "users" not in row["manifest"]["areas"]
    zf = zipfile.ZipFile(io.BytesIO(package_bytes(owner_conn, row["id"])))
    assert "data/users.json" not in zf.namelist()
    assert json.loads(zf.read("data/clips.json")) == []    # every clip belongs to someone
    assert json.loads(zf.read("data/group_members.json")) == []
    assert len(json.loads(zf.read("data/web_pages.json"))) == 1


async def test_csv_cannot_be_imported(make_client, owner_conn):
    c = await admin(make_client)
    seed_page = "insert into web_pages (slug, lang, title, description, body) values ('a', 'da', 'A', '', '')"
    owner_conn.execute(seed_page)
    row = await export(c, areas=["web"], format="csv")
    data = package_bytes(owner_conn, row["id"])
    assert b"slug" in zipfile.ZipFile(io.BytesIO(data)).read("data/web_pages.csv")
    r = await c.post(f"{BASE}/imports", files={"file": ("x.zip", data)})
    assert r.status_code == 400 and "CSV" in r.json()["detail"]


async def test_download_link_and_cancel(make_client, owner_conn):
    c = await admin(make_client)
    row = await export(c, areas=["web"])
    r = await c.get(f"{BASE}/{row['id']}/download")
    assert r.status_code == 200 and r.json()["url"].startswith("/api/_memory-storage/transfers/")
    r = await c.post(f"{BASE}/exports", json={"areas": ["web"]})
    tid = r.json()["id"]
    assert (await c.post(f"{BASE}/{tid}/cancel")).status_code == 200
    await jobs.run_pending()
    assert (await c.get(f"{BASE}/{tid}")).json()["status"] == "cancelled"
    assert (await c.get(f"{BASE}/{tid}/download")).status_code == 409


# ── Import ──────────────────────────────────────────────────────────────────

async def test_round_trip_dry_run_import_and_undo(make_client, owner_conn):
    c = await admin(make_client)
    await otp_login(make_client(), "22222222")
    ids = seed(owner_conn)
    row = await export(c, password="hemmelig-kode-123")
    data = package_bytes(owner_conn, row["id"])
    assert data.startswith(transfer.MAGIC)

    # Something happens to the data after the export.
    owner_conn.execute("delete from web_pages")
    owner_conn.execute("delete from clips")
    owner_conn.execute("update groups set name = 'Drift og support'")
    owner_conn.execute("delete from meta_dimensions")
    owner_conn.execute("delete from webhooks")

    r = await c.post(f"{BASE}/imports", files={"file": ("pakke.gaerx", data)})
    assert r.status_code == 400 and "krypteret" in r.json()["detail"]
    r = await c.post(f"{BASE}/imports", files={"file": ("pakke.gaerx", data)}, data={"password": "forkert-kode-123"})
    assert r.status_code == 400 and "Forkert adgangskode" in r.json()["detail"]
    up = await upload(c, data, "hemmelig-kode-123")
    assert up["status"] == "uploaded"
    assert {a["key"] for a in up["summary"]["areas"]} == set(CONFIG_AREAS)

    options = {"areas": ["web", "groups", "clips", "meta", "webhooks", "settings"]}
    dr = await dry_run(c, up["id"], **options)
    rep = dr["report"]
    assert rep["dry_run"] and not rep["blocked"] and dr["dry_run_current"]
    assert totals(rep, "web_pages")["created"] == 1
    assert totals(rep, "clips")["created"] == 1
    assert totals(rep, "groups")["different"] == 1         # the name differs, and skip keeps it
    assert totals(rep, "meta_nodes")["created"] == 2
    assert rep["differences"][0]["here"]["name"] == "Drift og support"
    assert owner_conn.execute("select count(*) from web_pages").fetchone()[0] == 0   # a dry run changes nothing

    # The real run needs the same options as the dry run.
    r = await c.post(f"{BASE}/{up['id']}/run", json={**options, "conflict": "overwrite"})
    assert r.status_code == 409
    r = await c.post(f"{BASE}/{up['id']}/run", json=options)
    assert r.status_code == 200, r.text
    await jobs.run_pending()
    done = (await c.get(f"{BASE}/{up['id']}")).json()
    assert done["status"] == "done", done
    assert done["undo_until"] is not None
    assert owner_conn.execute("select title from web_pages").fetchone()[0] == "Priser"
    assert owner_conn.execute("select name from groups").fetchone()[0] == "Drift og support"
    assert owner_conn.execute("select user_id from clips").fetchone()[0] == ids["user"]
    paths = [p for (p,) in owner_conn.execute("select path from meta_nodes order by depth")]
    assert paths == ["/org/drift", "/org/drift/support"]
    parent = owner_conn.execute("""select c.parent_id = p.id from meta_nodes c join meta_nodes p on p.path = '/org/drift'
                                   where c.path = '/org/drift/support'""").fetchone()[0]
    assert parent
    hook = owner_conn.execute("select active, disabled_reason, secret_enc <> '\\x00' from webhooks").fetchone()
    assert hook[0] is False and hook[2]                    # off, with a new signing secret
    assert owner_conn.execute("select value from settings where key = 'app_name'").fetchone()[0] == "Kundens app"
    audit = owner_conn.execute("""select count(*) from audit_log where action = 'create'
                                  and detail->>'transfer_id' = %s""", (str(up["id"]),)).fetchone()[0]
    assert audit == done["report"]["changes"]
    assert owner_conn.execute("select count(*) from audit_log where action = 'transfer.imported'").fetchone()[0] == 1

    r = await c.post(f"{BASE}/{up['id']}/undo")
    assert r.status_code == 200, r.text
    await jobs.run_pending()
    undone = (await c.get(f"{BASE}/{up['id']}")).json()
    assert undone["status"] == "undone" and undone["report"]["undo"]["failed"] == 0
    assert owner_conn.execute("select count(*) from web_pages").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from meta_nodes").fetchone()[0] == 0
    assert owner_conn.execute("select count(*) from data_transfer_changes").fetchone()[0] == 0
    assert (await c.post(f"{BASE}/{up['id']}/undo")).status_code == 409


async def test_conflict_options(make_client, owner_conn):
    c = await admin(make_client)
    owner_conn.execute("""insert into web_pages (slug, lang, title, description, body)
                          values ('om', 'da', 'Om os', 'Fra pakken', 'Tekst')""")
    data = package_bytes(owner_conn, (await export(c, areas=["web"]))["id"])
    owner_conn.execute("update web_pages set title = 'Om', description = ''")

    up = await upload(c, data)
    for conflict, expect in (("overwrite", ("Om os", "Fra pakken")), ("merge", ("Om", "Fra pakken"))):
        owner_conn.execute("update web_pages set title = 'Om', description = ''")
        if conflict == "merge":
            up = await upload(c, data)
        opts = {"areas": ["web"], "conflict": conflict}
        dr = await dry_run(c, up["id"], **opts)
        assert totals(dr["report"], "web_pages")["updated"] == 1
        assert (await c.post(f"{BASE}/{up['id']}/run", json=opts)).status_code == 200
        await jobs.run_pending()
        assert owner_conn.execute("select title, description from web_pages").fetchone() == expect

    up = await upload(c, data)
    opts = {"areas": ["web"], "conflict": "copy"}
    await dry_run(c, up["id"], **opts)
    assert (await c.post(f"{BASE}/{up['id']}/run", json=opts)).status_code == 200
    await jobs.run_pending()
    slugs = sorted(s for (s,) in owner_conn.execute("select slug from web_pages"))
    assert slugs == ["om", "om-import"]


def handmade(tables: dict, areas: list[str], blobs: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = {}
        for name, rows in tables.items():
            raw = json.dumps(rows).encode()
            zf.writestr(f"data/{name}.json", raw)
            info[name] = {"area": transfer.TABLE[name].area, "rows": len(rows), "file": f"data/{name}.json",
                          "sha256": hashlib.sha256(raw).hexdigest()}
        for fid, content in (blobs or {}).items():
            zf.writestr(f"blobs/{fid}", content)
        zf.writestr("manifest.json", json.dumps({"format": "gaer-transfer", "version": 1, "kind": "json",
                                                 "areas": areas, "personal": "with", "tables": info}))
    return buf.getvalue()


async def test_errors_block_all_or_nothing(make_client, owner_conn):
    c = await admin(make_client)
    page = {"id": 1, "slug": "ok", "lang": "da", "title": "OK", "description": "", "body": "", "published": True}
    bad = {**page, "id": 2, "slug": "forkert", "lang": "xx"}
    up = await upload(c, handmade({"web_pages": [page, bad]}, ["web"]))
    dr = await dry_run(c, up["id"], areas=["web"])
    rep = dr["report"]
    assert rep["blocked"] and rep["totals"]["failed"] == 1 and rep["totals"]["created"] == 1
    assert "web_pages_lang_check" in rep["errors"][0]["message"]
    r = await c.post(f"{BASE}/{up['id']}/run", json={"areas": ["web"]})
    assert r.status_code == 409

    await dry_run(c, up["id"], areas=["web"], on_error="continue")
    r = await c.post(f"{BASE}/{up['id']}/run", json={"areas": ["web"], "on_error": "continue"})
    assert r.status_code == 200
    await jobs.run_pending()
    assert [s for (s,) in owner_conn.execute("select slug from web_pages")] == ["ok"]


async def test_tampered_package_is_refused(make_client):
    c = await admin(make_client)
    data = bytearray(handmade({"web_pages": [{"id": 1, "slug": "a", "lang": "da", "title": "A", "description": "",
                                              "body": ""}]}, ["web"]))
    buf = io.BytesIO(bytes(data))
    zf = zipfile.ZipFile(buf)
    manifest = json.loads(zf.read("manifest.json"))
    manifest["tables"]["web_pages"]["sha256"] = "0" * 64
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z2:
        for n in zf.namelist():
            z2.writestr(n, json.dumps(manifest) if n == "manifest.json" else zf.read(n))
    r = await c.post(f"{BASE}/imports", files={"file": ("x.zip", out.getvalue())})
    assert r.status_code == 400 and "Kontrolsummen" in r.json()["detail"]
    r = await c.post(f"{BASE}/imports", files={"file": ("x.zip", b"ikke en zip")})
    assert r.status_code == 400


async def test_missing_users_and_roles(make_client, owner_conn):
    c = await admin(make_client)
    me = owner_conn.execute("select id from users where phone = '+4511111111'").fetchone()[0]
    users = [{"id": 50, "email": "ny@example.com", "phone": None, "display_name": "Ny", "role": "admin",
              "admin_roles": ["fuld"], "audience": "internal", "locale": None, "time_zone": None, "blocked_at": None},
             {"id": 51, "email": None, "phone": "+4511111111", "display_name": "Mig", "role": "user", "admin_roles": [],
              "audience": "internal", "locale": None, "time_zone": None, "blocked_at": "2026-01-01T00:00:00+00:00"}]
    clips = [{"id": 1, "user_id": 50, "source": "manual", "title": "A", "body": "", "url": None,
              "created_at": "2026-01-01T00:00:00+00:00"},
             {"id": 2, "user_id": 51, "source": "manual", "title": "B", "body": "", "url": None,
              "created_at": "2026-01-02T00:00:00+00:00"}]
    data = handmade({"users": users, "clips": clips}, ["users", "roles", "clips"])

    # Only clips: the unknown owner is skipped, the known one (matched on phone) is found.
    up = await upload(c, data)
    rep = (await dry_run(c, up["id"], areas=["clips"], missing="skip"))["report"]
    assert totals(rep, "clips")["created"] == 1 and totals(rep, "clips")["skipped"] == 1
    rep = (await dry_run(c, up["id"], areas=["clips"], missing="create"))["report"]
    assert totals(rep, "clips")["created"] == 2 and totals(rep, "users")["created"] == 1
    opts = {"areas": ["clips"], "missing": "create"}
    assert (await c.post(f"{BASE}/{up['id']}/run", json=opts)).status_code == 200
    await jobs.run_pending()
    new = owner_conn.execute("select role, admin_roles from users where email = 'ny@example.com'").fetchone()
    assert new == ("user", [])                             # created as an owner, never with the package's roles

    # With Brugere and Admin-roller and overwrite: others get the roles, the importer is never blocked or demoted.
    up = await upload(c, data)
    opts = {"areas": ["users", "roles"], "conflict": "overwrite"}
    await dry_run(c, up["id"], **opts)
    assert (await c.post(f"{BASE}/{up['id']}/run", json=opts)).status_code == 200
    await jobs.run_pending()
    assert owner_conn.execute("select role, admin_roles from users where email = 'ny@example.com'").fetchone() == \
        ("admin", ["fuld"])
    mine = owner_conn.execute("select role, blocked_at, display_name from users where id = %s", (me,)).fetchone()
    assert mine[0] == "admin" and mine[1] is None and mine[2] == "Mig"


async def test_file_content_round_trip(make_client, owner_conn):
    c = await admin(make_client)
    await get_storage().put("shared/obj-1", b"hej verden", "text/plain")
    owner_conn.execute("""insert into files (id, storage_key, filename, content_type, size_bytes, kind, space, folder)
                          values (gen_random_uuid(), 'shared/obj-1', 'hej.txt', 'text/plain', 10, 'file', 'shared', '/')""")
    row = await export(c, areas=["files"], include_blobs=True)
    assert row["manifest"]["blobs"] == {"count": 1, "bytes": 10}
    data = package_bytes(owner_conn, row["id"])
    owner_conn.execute("delete from files")

    up = await upload(c, data)
    opts = {"areas": ["files"]}
    rep = (await dry_run(c, up["id"], **opts))["report"]
    assert totals(rep, "files")["created"] == 1
    assert not [k for k in get_storage().objects if k.startswith("shared/") and k != "shared/obj-1"]
    assert (await c.post(f"{BASE}/{up['id']}/run", json=opts)).status_code == 200
    await jobs.run_pending()
    key = owner_conn.execute("select storage_key from files where filename = 'hej.txt'").fetchone()[0]
    assert key != "shared/obj-1" and get_storage().objects[key][0] == b"hej verden"

    # Without the content the file is skipped, not an error.
    up = await upload(c, package_bytes(owner_conn, (await export(c, areas=["files"]))["id"]))
    owner_conn.execute("delete from files")
    rep = (await dry_run(c, up["id"], **opts))["report"]
    assert totals(rep, "files")["skipped"] == 1 and not rep["blocked"]


async def test_cleanup_and_erasure_delete_packages(make_client, owner_conn):
    c = await admin(make_client)
    await otp_login(make_client(), "22222222")
    seed(owner_conn)
    row = await export(c)
    key = owner_conn.execute("select storage_key from data_transfers where id = %s", (row["id"],)).fetchone()[0]
    assert key in get_storage().objects

    from app import gdpr
    from app.logs import RequestMeta
    await gdpr.erase_user(owner_conn.execute("select id from users where phone = '+4522222222'").fetchone()[0],
                          actor_id=None, meta=RequestMeta(), reason="test")
    assert key not in get_storage().objects
    assert (await c.get(f"{BASE}/{row['id']}")).json()["status"] == "expired"

    row = await export(c, areas=["web"])
    owner_conn.execute("update data_transfers set expires_at = now() - interval '1 minute' where id = %s", (row["id"],))
    await transfer.cleanup_job({}, {})
    assert (await c.get(f"{BASE}/{row['id']}")).json()["status"] == "expired"


@pytest.mark.parametrize("password", ["", "kort"])
async def test_export_password_rules(make_client, password):
    c = await admin(make_client)
    r = await c.post(f"{BASE}/exports", json={"areas": ["web"], "password": password})
    assert r.status_code == (200 if not password else 400)
