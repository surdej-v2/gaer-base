"""Mapper (app/mapper.py, /api/mapper): folders in ~/mapper, shortcuts as URL objects, own titles, pins, import of
browser favourites in one transaction, saved views, the frame check for previews, audit, GDPR and the CSP."""

import dataclasses
import io
import json
import zipfile

import httpx
import pytest

from app import config, mapper
from tests.helpers import full_user, otp_login


async def _users(make_client):
    admin = make_client()
    await full_user(admin, "11111111")  # the first user is the super admin (meta admin with a second factor)
    a = make_client()
    await otp_login(a, "22222222")
    b = make_client()
    await otp_login(b, "33333333")
    return admin, a, b


async def test_folders_and_shortcuts_are_private_and_audited(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    assert (await a.get("/api/mapper")).json() == {"root": None, "folders": [], "shortcuts": [], "truncated": False}

    r = await a.post("/api/mapper/folders", json={"label": "Arbejde"})
    assert r.status_code == 200, r.text
    work = r.json()
    assert work["parent_id"] is None and work["depth"] == 1 and work["pinned"] is False
    drift = (await a.post("/api/mapper/folders", json={"label": "Drift", "parent_id": work["id"]})).json()
    assert drift["parent_id"] == work["id"] and drift["depth"] == 2

    # The root is ~/mapper in the user's private tree.
    path = owner_conn.execute("select path from meta_nodes where id = %s", (drift["id"],)).fetchone()[0]
    assert path.endswith("/mapper/arbejde/drift") and path.startswith("/~/")

    r = await a.post("/api/mapper/shortcuts", json={"url": "https://Status.example.com/?utm_source=x", "title": "Status",
                                                    "folder_id": drift["id"]})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["url"] == "https://status.example.com" and s["host"] == "status.example.com"
    assert s["folders"] == [drift["id"]] and s["created"] is True and s["mine"] is True
    top = (await a.post("/api/mapper/shortcuts", json={"url": "https://example.org/a", "title": ""})).json()
    assert top["folders"] == [None] and top["title"] == "example.org"
    assert (await a.post("/api/mapper/shortcuts", json={"url": "javascript:alert(1)"})).status_code == 400
    assert (await a.post("/api/mapper/shortcuts", json={"url": "https://example.org/*"})).status_code == 400

    state = (await a.get("/api/mapper")).json()
    assert state["root"]["label"] == "Mapper"
    assert [(f["label"], f["parent_id"]) for f in state["folders"]] == [("Arbejde", None), ("Drift", work["id"])]
    assert [x["title"] for x in state["shortcuts"]] == ["Status", "example.org"]

    # Another user sees nothing of it and cannot use the folders.
    assert (await b.get("/api/mapper")).json()["root"] is None
    assert (await b.post("/api/mapper/shortcuts", json={"url": "https://x.dk", "folder_id": drift["id"]})).status_code == 404
    assert (await b.patch(f"/api/mapper/folders/{drift['id']}", json={"label": "X"})).status_code == 404
    assert (await b.delete(f"/api/mapper/shortcuts/{s['id']}")).status_code == 404

    # Renaming a folder moves its path with the name; the root itself cannot be changed.
    r = await a.patch(f"/api/mapper/folders/{drift['id']}", json={"label": "Drift og support"})
    assert r.json()["label"] == "Drift og support"
    assert owner_conn.execute("select path from meta_nodes where id = %s", (drift["id"],)).fetchone()[0].endswith("/drift-og-support")
    root_id = state["root"]["id"]
    assert (await a.patch(f"/api/mapper/folders/{root_id}", json={"label": "X"})).status_code == 400
    # Move to the top level (parent_id null).
    assert (await a.patch(f"/api/mapper/folders/{drift['id']}", json={"parent_id": None})).json()["parent_id"] is None

    tables = {r[0] for r in owner_conn.execute("select table_name from audit_log where actor_user_id = (select id from users where phone like '%%22222222')")}
    assert {"meta_nodes", "meta_objects"} <= tables


async def test_own_titles_move_remove_and_pins(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    fa = (await a.post("/api/mapper/folders", json={"label": "Læs"})).json()
    fb = (await b.post("/api/mapper/folders", json={"label": "Senere"})).json()
    sa = (await a.post("/api/mapper/shortcuts", json={"url": "https://example.com/doc", "title": "A's titel", "folder_id": fa["id"]})).json()
    sb = (await b.post("/api/mapper/shortcuts", json={"url": "https://example.com/doc/", "title": "B's titel", "folder_id": fb["id"]})).json()
    assert sb["id"] == sa["id"] and sb["created"] is False
    assert sb["title"] == "B's titel" and sb["mine"] is False
    assert (await a.get("/api/mapper")).json()["shortcuts"][0]["title"] == "A's titel"

    # B renames: only B's title changes. A renames: the object's title (B keeps their own).
    assert (await b.patch(f"/api/mapper/shortcuts/{sb['id']}", json={"title": "B2"})).json()["title"] == "B2"
    assert (await a.patch(f"/api/mapper/shortcuts/{sa['id']}", json={"title": "A2"})).json()["title"] == "A2"
    assert owner_conn.execute("select title from meta_objects where id = %s", (sa["id"],)).fetchone()[0] == "A2"
    assert (await b.get("/api/mapper")).json()["shortcuts"][0]["title"] == "B2"

    # Move between folders, then remove from the last one: the object stays for A.
    other = (await b.post("/api/mapper/folders", json={"label": "Andet"})).json()
    r = await b.patch(f"/api/mapper/shortcuts/{sb['id']}", json={"move": {"from_id": fb["id"], "to_id": other["id"]}})
    assert r.status_code == 200, r.text
    assert r.json()["folders"] == [other["id"]]
    r = await b.delete(f"/api/mapper/shortcuts/{sb['id']}?folder={other['id']}")
    assert r.json() == {"id": sb["id"], "removed": 1, "deleted": False, "in_mapper": False}
    assert owner_conn.execute("select count(*) from mapper_titles").fetchone()[0] == 0
    assert (await b.get("/api/mapper")).json()["shortcuts"] == []

    # A removes it everywhere: the last tag is gone, so is the object.
    r = await a.delete(f"/api/mapper/shortcuts/{sa['id']}?everywhere=true")
    assert r.json()["deleted"] is True
    assert owner_conn.execute("select count(*) from meta_objects").fetchone()[0] == 0

    # Pins, and deleting a folder with content needs force.
    assert (await a.put(f"/api/mapper/folders/{fa['id']}/pin", json={"pinned": True})).json()["pinned"] is True
    assert (await a.get("/api/mapper")).json()["folders"][0]["pinned"] is True
    assert (await b.put(f"/api/mapper/folders/{fa['id']}/pin", json={"pinned": True})).status_code == 404
    await a.post("/api/mapper/shortcuts", json={"url": "https://example.net", "folder_id": fa["id"]})
    assert (await a.delete(f"/api/mapper/folders/{fa['id']}")).status_code == 409
    assert (await a.delete(f"/api/mapper/folders/{fa['id']}?force=true")).status_code == 200
    assert owner_conn.execute("select count(*) from mapper_pins").fetchone()[0] == 0

    actions = owner_conn.execute("select action, table_name from audit_log where table_name like 'mapper_%' order by id").fetchall()
    assert ("create", "mapper_titles") in actions and ("update", "mapper_titles") in actions and ("create", "mapper_pins") in actions


async def test_other_tags_are_emner(make_client):
    admin, a, b = await _users(make_client)
    await admin.post("/api/admin/meta/dimensions", json={"slug": "emne", "label": "Emner", "color": "#2563EB"})
    drift = (await admin.post("/api/meta/nodes", json={"dimension": "emne", "label": "Drift"})).json()
    folder = (await a.post("/api/mapper/folders", json={"label": "Arbejde"})).json()
    s = (await a.post("/api/mapper/shortcuts", json={"url": "https://status.example.com", "title": "Status",
                                                     "folder_id": folder["id"]})).json()
    await a.post("/api/meta/objects", json={"kind": "url", "ref": "https://status.example.com", "title": "Status",
                                            "node_ids": [drift["id"]]})
    tags = (await a.get("/api/mapper")).json()["shortcuts"][0]["tags"]
    assert [(t["label"], t["dimension"], t["dimension_label"]) for t in tags] == [("Drift", "emne", "Emner")]
    assert s["tags"] == []


async def test_import_bookmarks_in_one_transaction(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    body = {
        "target": "new", "keep_structure": True, "skip_duplicates": True, "pin_bar": True,
        "folders": [
            {"key": "1", "parent": None, "label": "Bogmærkelinjen", "bar": True},
            {"key": "5", "parent": "1", "label": "Nyheder"},
            {"key": "2", "parent": None, "label": "Andre bogmærker"},
            # Deeper than a tree may go: the last levels are folded into the deepest folder there is room for.
            *[{"key": f"d{i}", "parent": f"d{i - 1}" if i else "2", "label": f"Niveau {i}"} for i in range(8)],
        ],
        "items": [
            {"folder": "1", "url": "https://github.com/", "title": "GitHub"},
            {"folder": "5", "url": "https://dr.dk", "title": "DR"},
            {"folder": "5", "url": "https://dr.dk/#top", "title": "DR igen"},    # the same address: a duplicate
            {"folder": "2", "url": "javascript:void(0)", "title": "Bookmarklet"},  # not a web address
            {"folder": "d7", "url": "https://deep.example.com", "title": "Dybt"},
            {"folder": None, "url": "https://loose.example.com", "title": ""},
        ],
    }
    r = await a.post("/api/mapper/import", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["shortcuts_added"] == 4 and res["shortcuts_created"] == 4 and res["duplicates"] == 1 and res["invalid"] == 1
    assert res["pinned"] == 1 and res["folders_created"] == 1 + 3 + 5  # Fra browseren, 3 folders, 5 of the 8 levels (depth 4–8)

    state = (await a.get("/api/mapper")).json()
    by_id = {f["id"]: f for f in state["folders"]}
    target = by_id[res["target_id"]]
    assert target["label"] == "Fra browseren" and target["parent_id"] is None
    bar = next(f for f in state["folders"] if f["label"] == "Bogmærkelinjen")
    assert bar["pinned"] and bar["parent_id"] == target["id"]
    assert max(f["depth"] for f in state["folders"]) == 7  # ~/mapper is depth 1 of 8
    deep = next(s for s in state["shortcuts"] if s["title"] == "Dybt")
    assert by_id[deep["folders"][0]]["label"] == "Niveau 4"
    loose = next(s for s in state["shortcuts"] if s["url"] == "https://loose.example.com")
    assert loose["folders"] == [target["id"]] and loose["title"] == "loose.example.com"

    # Importing again reuses the folders and skips what is there already.
    res2 = (await a.post("/api/mapper/import", json=body)).json()
    assert res2["folders_created"] == 0 and res2["shortcuts_added"] == 0 and res2["duplicates"] == 5

    # Without the structure, at the top level: everything straight under the root.
    r = await b.post("/api/mapper/import", json={"target": "top", "keep_structure": False, "folders": body["folders"],
                                                  "items": body["items"][:2]})
    assert r.json()["folders_created"] == 0 and r.json()["target_id"] is None
    assert [s["folders"] for s in (await b.get("/api/mapper")).json()["shortcuts"]] == [[None], [None]]

    row = owner_conn.execute("select detail from audit_log where action = 'mapper.import' order by id limit 1").fetchone()[0]
    assert row["shortcuts_added"] == 4 and row["duplicates"] == 1
    assert owner_conn.execute("select count(*) from audit_log where action = 'create' and table_name = 'meta_objects'").fetchone()[0] == 4

    # Validation: limits and unique folder keys.
    assert (await a.post("/api/mapper/import", json={"folders": [{"key": "1", "label": "x"}, {"key": "1", "label": "y"}]})).status_code == 422


async def test_views_mine_and_shared(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    spec = {"layout": "table", "sort": "site", "columns": ["site", "added"], "filter": "github"}
    mine = (await a.post("/api/mapper/views", json={"name": "Mine tabeller", "spec": spec})).json()
    assert mine["mine"] and not mine["shared"] and mine["spec"] == spec
    shared = (await a.post("/api/mapper/views", json={"name": "Til teamet", "shared": True,
                                                      "spec": {"layout": "grid", "sort": "name"}})).json()
    assert shared["spec"] == {"layout": "grid", "sort": "name", "columns": [], "filter": ""}
    assert (await a.post("/api/mapper/views", json={"name": "X", "spec": {"layout": "kanban"}})).status_code == 422

    seen = (await b.get("/api/mapper/views")).json()
    assert [(v["name"], v["mine"]) for v in seen] == [("Til teamet", False)]
    assert (await b.patch(f"/api/mapper/views/{shared['id']}", json={"name": "Mit nu"})).status_code == 403
    assert (await b.delete(f"/api/mapper/views/{shared['id']}")).status_code == 403
    assert (await b.patch(f"/api/mapper/views/{mine['id']}", json={"name": "X"})).status_code == 404

    r = await a.patch(f"/api/mapper/views/{mine['id']}", json={"spec": {"layout": "list", "sort": "added"}})
    assert r.json()["spec"] == {"layout": "list", "sort": "added", "columns": [], "filter": ""}

    # A meta admin may remove a shared view; the owner removes their own.
    assert (await admin.delete(f"/api/mapper/views/{shared['id']}")).status_code == 200
    assert (await a.delete(f"/api/mapper/views/{mine['id']}")).status_code == 200
    actions = [r[0] for r in owner_conn.execute("select action from audit_log where table_name = 'mapper_views' order by id")]
    assert actions == ["create", "create", "update", "delete", "delete"]


async def test_gdpr_export_and_erasure(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    f = (await a.post("/api/mapper/folders", json={"label": "Privat"})).json()
    await a.put(f"/api/mapper/folders/{f['id']}/pin", json={"pinned": True})
    await b.post("/api/mapper/shortcuts", json={"url": "https://example.com", "title": "B først"})
    await a.post("/api/mapper/shortcuts", json={"url": "https://example.com", "title": "Min titel", "folder_id": f["id"]})
    await a.post("/api/mapper/views", json={"name": "Delt", "shared": True, "spec": {}})
    data = json.loads(zipfile.ZipFile(io.BytesIO((await a.get("/api/me/export")).content)).read("data.json"))
    assert [t["title"] for t in data["mapper_titles"]] == ["Min titel"]
    assert len(data["mapper_pins"]) == 1 and data["mapper_views"][0]["name"] == "Delt"
    assert (await a.post("/api/me/delete", json={"confirm_phone": "22222222"})).status_code == 200
    for table in ("mapper_titles", "mapper_pins", "mapper_views"):
        assert owner_conn.execute(f"select count(*) from {table}").fetchone()[0] == 0
    assert [s["title"] for s in (await b.get("/api/mapper")).json()["shortcuts"]] == ["B først"]


@pytest.fixture
def pages(monkeypatch):
    answers: dict[str, httpx.Response] = {}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return answers.get(str(request.url), httpx.Response(200, headers={"content-type": "text/html"}))

    monkeypatch.setattr(mapper, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(mapper, "_frame_cache", {})
    monkeypatch.setattr(mapper, "_frame_calls", {})
    return answers, seen


async def test_frame_check(make_client, pages, monkeypatch):
    answers, seen = pages
    c = make_client()
    await otp_login(c, "44444444")
    answers["https://deny.example.com/"] = httpx.Response(200, headers={"x-frame-options": "SAMEORIGIN"})
    answers["https://csp.example.com/"] = httpx.Response(200, headers={"content-security-policy": "default-src 'self'; frame-ancestors 'self'"})
    answers["https://open.example.com/"] = httpx.Response(200, headers={"content-security-policy": "frame-ancestors *", "content-type": "text/html; charset=utf-8"})
    answers["https://docs.example.com/a.pdf"] = httpx.Response(200, headers={"content-type": "application/pdf"})
    answers["https://move.example.com/"] = httpx.Response(302, headers={"location": "/new"})
    answers["https://move.example.com/new"] = httpx.Response(200, headers={"content-type": "application/pdf"})
    answers["https://down.example.com/"] = httpx.Response(302, headers={"location": "http://down.example.com/"})
    answers["https://file.example.com/"] = httpx.Response(200, headers={"content-disposition": "attachment; filename=x.zip"})

    async def check(url):
        r = await c.get("/api/mapper/frame-check", params={"url": url})
        assert r.status_code == 200, r.text
        return r.json()

    assert (await check("https://deny.example.com/"))["framable"] is False
    assert (await check("https://csp.example.com/"))["framable"] is False
    assert (await check("https://open.example.com/")) | {"reason": None} == {
        "url": "https://open.example.com/", "framable": True, "kind": "html", "reason": None, "final_url": "https://open.example.com/"}
    assert (await check("https://docs.example.com/a.pdf"))["kind"] == "pdf"
    moved = await check("https://move.example.com/")
    assert moved["framable"] and moved["kind"] == "pdf" and moved["final_url"] == "https://move.example.com/new"
    assert (await check("https://down.example.com/"))["framable"] is False
    assert (await check("https://file.example.com/"))["framable"] is False
    http = await check("http://plain.example.com/")
    assert http["framable"] is False and "http" in http["reason"]
    n = len(seen)
    await check("https://deny.example.com/")  # cached
    assert len(seen) == n

    # The SSRF guard: no requests to private addresses (tests allow them for webhooks; production does not).
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, webhook_allow_private=False))
    r = await c.get("/api/mapper/frame-check", params={"url": "https://127.0.0.1/admin"})
    assert r.status_code == 400 and "internt" in r.json()["detail"]
    assert (await c.get("/api/mapper/frame-check", params={"url": "ftp://example.com"})).status_code == 400
    assert len(seen) == n

    # The name is resolved once and the request goes to that checked address (no second lookup to rebind),
    # with the Host header and the TLS name (SNI) kept as the name so the certificate is checked as usual.
    pinned: list[tuple] = []

    def record(request: httpx.Request) -> httpx.Response:
        pinned.append((str(request.url), request.headers["host"], request.extensions.get("sni_hostname")))
        return httpx.Response(200, headers={"content-type": "text/html"})

    monkeypatch.setattr(mapper, "transport", httpx.MockTransport(record))
    monkeypatch.setattr(mapper.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    r = await c.get("/api/mapper/frame-check", params={"url": "https://pinned.example.com/side?x=1"})
    assert r.status_code == 200 and r.json()["framable"] is True
    assert r.json()["final_url"] == "https://pinned.example.com/side?x=1"
    assert pinned == [("https://93.184.216.34/side?x=1", "pinned.example.com", "pinned.example.com")]
    # A name that resolves to an internal address is refused before any request is made.
    monkeypatch.setattr(mapper.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    r = await c.get("/api/mapper/frame-check", params={"url": "https://rebind.example.com/"})
    assert r.status_code == 400 and "internt" in r.json()["detail"]
    assert len(pinned) == 1


async def test_feature_and_csp(client):
    from app.features_catalog import CATALOG
    assert "mapper" in {f.key for f in CATALOG}  # migration 0040 adds it in ring 1; /admin/features moves it out
    for path in ("/extension/mapper", "/m/mapper"):
        assert "frame-src 'self' https: " in (await client.get(path)).headers["content-security-policy"], path
    assert "frame-src 'self' https://*.officeapps" in (await client.get("/m/klip")).headers["content-security-policy"]
    assert "frame-src 'self' https://*.officeapps" in (await client.get("/m")).headers["content-security-policy"]
    assert "frame-src 'self' https://*.officeapps" in (await client.get("/extension")).headers["content-security-policy"]
    assert "frame-src 'self' https://*.officeapps" in (await client.get("/klip")).headers["content-security-policy"]


async def test_folder_properties_and_ordering(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    work = (await a.post("/api/mapper/folders", json={"label": "Arbejde"})).json()
    kids = [(await a.post("/api/mapper/folders", json={"label": n, "parent_id": work["id"]})).json() for n in ("Bravo", "Alfa", "Charlie")]

    # Egenskaber: English label, sort key, subfolder order, shortcut sort, layout.
    r = await a.patch(f"/api/mapper/folders/{work['id']}", json={
        "label": "Arbejdet", "en_label": "Work", "sort_key": "010", "child_sort": "sort_key", "shortcut_sort": "name", "layout": "grid"})
    assert r.status_code == 200, r.text
    f = r.json()
    assert (f["label"], f["en_label"], f["sort_key"], f["child_sort"], f["shortcut_sort"], f["layout"]) == (
        "Arbejdet", "Work", "010", "sort_key", "name", "grid")
    labels = owner_conn.execute("select labels from meta_nodes where id = %s", (work["id"],)).fetchone()[0]
    assert labels == {"en": "Work"}
    # Cleared again with null / "".
    f = (await a.patch(f"/api/mapper/folders/{work['id']}", json={"en_label": "", "layout": None, "sort_key": ""})).json()
    assert f["en_label"] is None and f["layout"] is None and f["sort_key"] is None and f["shortcut_sort"] == "name"
    assert (await a.patch(f"/api/mapper/folders/{work['id']}", json={"layout": "kanban"})).status_code == 422

    # The root takes the orderings and layout, not a name.
    root_id = (await a.get("/api/mapper")).json()["root"]["id"]
    assert (await a.patch(f"/api/mapper/folders/{root_id}", json={"child_sort": "manual", "layout": "table"})).status_code == 200
    root = (await a.get("/api/mapper")).json()["root"]
    assert root["child_sort"] == "manual" and root["layout"] == "table"
    assert (await a.patch(f"/api/mapper/folders/{root_id}", json={"label": "X"})).status_code == 400

    # Manual order of subfolders (up/down in Egenskaber).
    order = [kids[2]["id"], kids[0]["id"], kids[1]["id"]]
    r = await a.put(f"/api/mapper/folders/{work['id']}/children-order", json={"ids": order})
    assert r.status_code == 200, r.text
    folders = {x["id"]: x for x in (await a.get("/api/mapper")).json()["folders"]}
    assert sorted(order, key=lambda i: folders[i]["sort_order"]) == order and folders[work["id"]]["child_sort"] == "manual"
    assert (await a.put(f"/api/mapper/folders/{work['id']}/children-order", json={"ids": [root_id]})).status_code == 400
    assert (await b.put(f"/api/mapper/folders/{work['id']}/children-order", json={"ids": order})).status_code == 404

    # Manual order of shortcuts (drag and drop): positions per folder, the folder's sort becomes manual.
    ids = [(await a.post("/api/mapper/shortcuts", json={"url": f"https://ex{i}.dk", "folder_id": work["id"]})).json()["id"] for i in range(3)]
    r = await a.put(f"/api/mapper/folders/{work['id']}/shortcut-order", json={"ids": [ids[2], ids[0], ids[1]]})
    assert r.status_code == 200, r.text
    st = (await a.get("/api/mapper")).json()
    pos = {s["id"]: s["positions"][s["folders"].index(work["id"])] for s in st["shortcuts"]}
    assert (pos[ids[2]], pos[ids[0]], pos[ids[1]]) == (0, 1, 2)
    assert next(x for x in st["folders"] if x["id"] == work["id"])["shortcut_sort"] == "manual"
    assert (await a.put(f"/api/mapper/folders/{kids[0]['id']}/shortcut-order", json={"ids": ids})).status_code == 400

    reorders = owner_conn.execute("select detail->>'kind' from audit_log where action = 'mapper.reorder' order by id").fetchall()
    assert [r[0] for r in reorders] == ["folders", "shortcuts"]
    assert owner_conn.execute("select count(*) from audit_log where table_name = 'mapper_folder_settings'").fetchone()[0] >= 3


async def test_copy_and_move(make_client, owner_conn):
    admin, a, b = await _users(make_client)
    src = (await a.post("/api/mapper/folders", json={"label": "Kilde"})).json()
    sub = (await a.post("/api/mapper/folders", json={"label": "Under", "parent_id": src["id"]})).json()
    dst = (await a.post("/api/mapper/folders", json={"label": "Mål"})).json()
    s1 = (await a.post("/api/mapper/shortcuts", json={"url": "https://one.dk", "title": "Et", "folder_id": src["id"]})).json()
    s2 = (await a.post("/api/mapper/shortcuts", json={"url": "https://two.dk", "title": "To", "folder_id": sub["id"]})).json()
    await a.patch(f"/api/mapper/folders/{src['id']}", json={"en_label": "Source", "shortcut_sort": "name"})

    # Copy a shortcut = one more tag; twice into the same folder is refused.
    r = await a.post(f"/api/mapper/shortcuts/{s1['id']}/copy", json={"to_id": dst["id"]})
    assert r.status_code == 200 and sorted(r.json()["folders"]) == sorted([src["id"], dst["id"]])
    assert (await a.post(f"/api/mapper/shortcuts/{s1['id']}/copy", json={"to_id": dst["id"]})).status_code == 409
    assert (await b.post(f"/api/mapper/shortcuts/{s1['id']}/copy", json={"to_id": None})).status_code == 404

    # Copy a folder: the subtree with properties and shortcuts, "(kopi)", in the same parent twice → unique paths.
    r = await a.post(f"/api/mapper/folders/{src['id']}/copy", json={"to_id": None})
    assert r.status_code == 200, r.text
    assert r.json() | {"id": 0} == {"id": 0, "label": "Kilde (kopi)", "folders": 2, "shortcuts": 2}
    again = (await a.post(f"/api/mapper/folders/{src['id']}/copy", json={"to_id": None})).json()
    st = (await a.get("/api/mapper")).json()
    folders = {f["id"]: f for f in st["folders"]}
    copy = folders[r.json()["id"]]
    assert copy["en_label"] == "Source" and copy["shortcut_sort"] == "name" and copy["parent_id"] is None
    assert [f["label"] for f in st["folders"] if f["parent_id"] == copy["id"]] == ["Under"]
    by_title = {s["title"]: s for s in st["shortcuts"]}
    assert copy["id"] in by_title["Et"]["folders"] and again["id"] in by_title["Et"]["folders"]
    paths = [p[0] for p in owner_conn.execute("select path from meta_nodes where label = 'Kilde (kopi)' order by id")]
    assert paths[0].endswith("/kilde-kopi") and paths[1].endswith("/kilde-kopi-2")
    # Into itself or below itself: refused, for copy and move.
    assert (await a.post(f"/api/mapper/folders/{src['id']}/copy", json={"to_id": sub["id"]})).status_code == 400
    r = await a.patch(f"/api/mapper/folders/{src['id']}", json={"parent_id": sub["id"]})
    assert r.status_code == 400 and "sig selv" in r.json()["detail"]
    assert (await a.patch(f"/api/mapper/folders/{src['id']}", json={"parent_id": src["id"]})).status_code == 400

    # Move a folder (path rewrite) and a shortcut (retag).
    r = await a.patch(f"/api/mapper/folders/{src['id']}", json={"parent_id": dst["id"]})
    assert r.status_code == 200 and r.json()["parent_id"] == dst["id"]
    assert owner_conn.execute("select path from meta_nodes where id = %s", (sub["id"],)).fetchone()[0].endswith("/mål/kilde/under")
    r = await a.patch(f"/api/mapper/shortcuts/{s2['id']}", json={"move": {"from_id": sub["id"], "to_id": dst["id"]}})
    assert dst["id"] in r.json()["folders"] and sub["id"] not in r.json()["folders"]  # the copies keep theirs

    actions = [r[0] for r in owner_conn.execute("select action from audit_log where action like 'mapper.%' order by id")]
    assert actions.count("mapper.copy") == 2
    via = owner_conn.execute("select count(*) from audit_log where detail->>'via' = 'mapper.copy'").fetchone()[0]
    assert via == 4  # two objects tagged in each of the two copies
