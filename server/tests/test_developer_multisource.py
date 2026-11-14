"""Gær Developer, fase 5: multisource sources (DS-10 … DS-17) — a graph of steps run level by level and in parallel,
merged on a key, pagination (offset, nextLink), foreach with batch calls, retries and HTTP 429 with Retry-After,
partial failures, a cache per step, masking before anything is merged, and the delegated extension point."""

import json

import httpx

from app import data_sources
from app.devkit import multisource
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"

USERS_SPEC = """
kilde: graph
navn: Microsoft Graph (app)
type: rest
base_url: https://graph.example.com
klassifikation: grøn
grænser: { kald_pr_minut: 600, timeout_s: 5, cache_min: 0 }
datasæt:
  - id: users
    navn: Brugere
    kald: GET /users
    rækker: $.value[*]
    nøgle: id
    felter:
      id:   { type: tekst, klassifikation: hvid }
      navn: { sti: displayName, klassifikation: rød }
      afd:  { sti: department, klassifikation: grøn }
"""


async def _ops(admin, ids):
    async def op(slug, definition):
        r = await admin.post(f"{DEV}/operations", json={"slug": slug, "name": slug, "definition": definition})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    liste = await op("ms_liste", {"source": {"dataset": ids["lejemaal"]}, "fields": ["id", "ejendom_id", "aarlig_leje"],
                                  "params": [{"name": "ejendom", "field": "ejendom_id"}], "sort": "id", "limit": 3, "max_limit": 3})
    ejendom = await op("ms_ejendom", {"source": {"dataset": ids["ejendomme"]}, "fields": ["id", "adresse"]})
    lejer = await op("ms_lejer", {"source": {"dataset": ids["lejemaal"]}, "fields": ["id", "lejer_navn", "status"],
                                  "params": [{"name": "ids", "field": "id", "op": "in"}], "max_limit": 100})
    return liste, ejendom, lejer


def _definition(liste, ejendom, lejer, **extra):
    return {"description": "Lejemål med ejendom og lejer", "key": "id",
            "params": [{"name": "ejendom", "type": "string"}],
            "steps": [{"id": "liste", "operation": liste, "args": {"ejendom": "$param.ejendom"}, "paginate": True, "ttl": 900},
                      {"id": "ejendom", "operation": ejendom, "after": ["liste"], "join": {"local": "ejendom_id", "foreign": "id"}},
                      {"id": "lejer", "operation": lejer, "foreach": "liste", "args": {"ids": "$item.id"},
                       "batch": {"param": "ids", "key": "id", "size": 4}, "ttl": 300}],
            **extra}


async def test_validation_refuses_cycles_and_bad_steps(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    liste, ejendom, lejer = await _ops(admin, ids)
    bad = {"steps": [{"id": "a", "operation": liste, "after": ["b"]}, {"id": "b", "operation": liste, "after": ["a"]}]}
    r = await admin.post(f"{DEV}/multisource", json={"slug": "x", "name": "x", "definition": bad})
    assert r.status_code == 422 and "ring" in r.json()["detail"]
    bad = {"steps": [{"id": "a", "operation": liste, "foreach": "a", "batch": {"param": "ejendom"}}]}
    r = await admin.post(f"{DEV}/multisource", json={"slug": "x", "name": "x", "definition": bad})
    assert r.status_code == 422
    bad = {"steps": [{"id": "a", "operation": ejendom}, {"id": "b", "operation": liste, "foreach": "a", "batch": {"param": "ejendom"}}]}
    r = await admin.post(f"{DEV}/multisource", json={"slug": "x", "name": "x", "definition": bad})
    assert r.status_code == 422 and "'in'" in r.json()["detail"]
    levels = multisource.levels((await multisource.validate(None, {"steps": []})) if False else [
        {"id": "a", "after": [], "foreach": None}, {"id": "b", "after": [], "foreach": None}, {"id": "c", "after": ["a", "b"], "foreach": None}])
    assert [[s["id"] for s in lvl] for lvl in levels] == [["a", "b"], ["c"]]


async def test_runs_levels_paginates_joins_batches_masks_and_caches(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    liste, ejendom, lejer = await _ops(admin, ids)
    r = await admin.post(f"{DEV}/multisource", json={"slug": "lejemaal_detaljer", "name": "Lejemål-detaljer",
                                                      "definition": _definition(liste, ejendom, lejer)})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    detail = (await admin.get(f"{DEV}/multisource/{mid}")).json()
    assert detail["levels"] == [["liste"], ["ejendom", "lejer"]]
    out = (await admin.post(f"{DEV}/multisource/{mid}/try", json={"args": {"ejendom": "E-1"}})).json()
    assert out["count"] == 12 and out["partial"] is False
    row = out["rows"][0]
    assert row["adresse"] == "Eksempelvej 1"                 # green: shown
    assert row["lejer_navn"] == "•••" and "-" in row["aarlig_leje"]   # masked in the steps, before the merge
    steps = {s["id"]: s for s in out["steps"]}
    assert steps["liste"]["calls"] == 4                       # 12 rows, 3 per page
    assert steps["lejer"]["calls"] == 3                       # 12 elements in batches of 4
    assert steps["liste"]["sample"] and steps["liste"]["ms"] >= 0
    again = (await admin.post(f"{DEV}/multisource/{mid}/try", json={"args": {"ejendom": "E-1"}})).json()
    s2 = {s["id"]: s for s in again["steps"]}
    assert s2["liste"]["cache"] == "hit" and s2["liste"]["calls"] == 0 and s2["ejendom"]["cache"] == "miss"
    assert again["rows"] == out["rows"]


def _graph(pages: list, fail_first: int = 0):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) <= fail_first:
            return httpx.Response(429, headers={"retry-after": "0"})
        page = pages[min(len(calls) - fail_first - 1, len(pages) - 1)]
        return httpx.Response(200, json=page)
    return httpx.MockTransport(handler), calls


async def _graph_source(admin, monkeypatch):
    async def check_host(host):
        return None
    monkeypatch.setattr(data_sources, "check_host", check_host)
    r = await admin.post("/api/admin/data/sources", json={"spec_yaml": USERS_SPEC})
    assert r.status_code == 201, r.text


async def test_http_step_with_nextlink_and_retry_after(make_client, owner_conn, monkeypatch):
    admin, auth, ids = await _setup(make_client, owner_conn)
    await _graph_source(admin, monkeypatch)
    pages = [{"value": [{"id": "u1", "displayName": "Jens", "department": "Drift"}],
              "@odata.nextLink": "https://graph.example.com/users?$skiptoken=2"},
             {"value": [{"id": "u2", "displayName": "Mette", "department": "Salg"}]}]
    mock, calls = _graph(pages, fail_first=1)
    monkeypatch.setattr(multisource, "transport", mock)
    definition = {"key": "id", "steps": [{"id": "brugere", "http": {"source": "graph", "dataset": "users"},
                                          "paginate": {"type": "nextLink", "next": "@odata.nextLink"}}]}
    mid = (await admin.post(f"{DEV}/multisource", json={"slug": "brugere", "name": "Brugere", "definition": definition})).json()["id"]
    out = (await admin.post(f"{DEV}/multisource/{mid}/try", json={})).json()
    assert [r["id"] for r in out["rows"]] == ["u1", "u2"]
    assert {r["navn"] for r in out["rows"]} == {"•••"} and out["rows"][0]["afd"] == "Drift"
    assert out["steps"][0]["throttled"] == 1 and len(calls) == 3
    # A nextLink to another host is refused.
    mock, _ = _graph([{"value": [{"id": "u1"}], "@odata.nextLink": "https://evil.example.org/x"}])
    monkeypatch.setattr(multisource, "transport", mock)
    out = await admin.post(f"{DEV}/multisource/{mid}/try", json={"cache": False})
    assert out.status_code == 424 and "værter" in out.json()["detail"]


async def test_partial_failures_and_delegated(make_client, owner_conn, monkeypatch):
    admin, auth, ids = await _setup(make_client, owner_conn)
    liste, ejendom, lejer = await _ops(admin, ids)
    await _graph_source(admin, monkeypatch)

    def handler(request):
        return httpx.Response(503)
    monkeypatch.setattr(multisource, "transport", httpx.MockTransport(handler))
    definition = {"key": "id", "partial": "mark", "limits": {"retries": 1},
                  "steps": [{"id": "liste", "operation": liste, "args": {"ejendom": "E-2"}},
                            {"id": "info", "http": {"source": "graph", "dataset": "users"}, "foreach": "liste"},
                            {"id": "mig", "delegated": True, "after": ["liste"]}]}
    mid = (await admin.post(f"{DEV}/multisource", json={"slug": "delvis", "name": "Delvis", "definition": definition})).json()["id"]
    out = (await admin.post(f"{DEV}/multisource/{mid}/try", json={})).json()
    assert out["partial"] is True and out["count"] == 3
    assert out["rows"][0]["_fejl"] == {"info": "Kilden svarede 503"}
    steps = {s["id"]: s for s in out["steps"]}
    assert "ikke tilgængelig endnu" in steps["mig"]["errors"][0]
    # With partial: fail, the first failing element stops the whole call.
    definition["partial"] = "fail"
    r = await admin.put(f"{DEV}/multisource/{mid}", json={"definition": definition, "submit": True})
    assert r.status_code == 200, r.text
    out = await admin.post(f"{DEV}/multisource/{mid}/try", json={})
    assert out.status_code == 424


async def test_multisource_as_mcp_tool(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    liste, ejendom, lejer = await _ops(admin, ids)
    mid = (await admin.post(f"{DEV}/multisource", json={"slug": "detaljer", "name": "Detaljer",
                                                         "definition": _definition(liste, ejendom, lejer)})).json()["id"]
    eid = (await admin.post(f"{DEV}/datastudie/endpoints", json={"slug": "lejedetaljer", "name": "Lejedetaljer"})).json()["id"]
    r = await admin.put(f"{DEV}/datastudie/endpoints/{eid}", json={"definition": {"name": "Lejedetaljer", "audience": "internal",
                        "tools": [{"name": "lejemaal_detaljer", "multisource": mid, "description": "Lejemål med ejendom og lejer"}]}})
    assert r.status_code == 200, r.text
    assert (await admin.post(f"{DEV}/artifacts/{eid}/submit", json={})).json()["status"] == "published"
    out = (await admin.post(f"{DEV}/mcp/lejedetaljer/call", json={"tool": "lejemaal_detaljer", "args": {"ejendom": "E-1"}})).json()
    assert not out["isError"], out
    assert out["result"]["count"] == 12 and out["result"]["rows"][0]["lejer_navn"] == "•••"
    log = owner_conn.execute("select args from mcp_log where tool = 'lejemaal_detaljer'").fetchone()[0]
    assert log == {"ejendom": "•••"}
    assert json.dumps(log)
