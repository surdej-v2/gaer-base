"""Gær Developer, fase 3: operations and the runtime (API-2, F-10, F-17 … F-21), Datastudie's guide to an MCP endpoint
on /mcp/<slug> (DS-3 … DS-9, MCP-3 … MCP-6, MCP-14), cache, rate limits and idempotency with and without Redis."""

import asyncio
import os
import shutil
import socket
import subprocess

import pytest

from app import config
from app.devkit import cache
from tests.helpers import full_user, otp_login
from tests.mcp_client import McpClient
from tests.mcp_helpers import mcp_token
from tests.test_developer_pipeline import _setup

DEV = "/api/admin/dev"


async def _op(admin, ids, **extra):
    definition = {"source": {"dataset": ids["lejemaal"]}, "fields": ["id", "ejendom_id", "aarlig_leje", "lejer_navn"],
                  "params": [{"name": "ejendom", "field": "ejendom_id", "op": "eq"}], "sort": "id", "limit": 5, **extra}
    r = await admin.post(f"{DEV}/operations", json={"slug": "soeg_lejemaal", "name": "Søg lejemål", "definition": definition})
    assert r.status_code == 201, r.text
    return r.json()


async def test_operation_is_masked_validated_and_paged(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    op = await _op(admin, ids)
    assert op["version"] == "1.0.0"
    r = await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"ejendom": "E-2"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["count"] == 5 and out["hasMore"] is True
    row = out["rows"][0]
    assert row["ejendom_id"] == "E-2" and row["lejer_navn"] == "•••" and "-" in row["aarlig_leje"]
    assert set(out["masked"]) == {"aarlig_leje", "lejer_navn"}
    r = await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"ejendom": "E-2", "limit": 100}})
    assert r.json()["count"] == 8
    assert (await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"x": 1}})).status_code == 422
    # A weaker masking than the classification, or a parameter on a masked field, is refused.
    bad = {"source": {"dataset": ids["lejemaal"]}, "fields": ["lejer_navn"], "masking": {"lejer_navn": "show"}}
    r = await admin.post(f"{DEV}/operations", json={"slug": "x1", "name": "x", "definition": bad})
    assert r.status_code == 422 and "rød" in r.json()["detail"]
    bad = {"source": {"dataset": ids["lejemaal"]}, "params": [{"name": "navn", "field": "lejer_navn"}]}
    r = await admin.post(f"{DEV}/operations", json={"slug": "x2", "name": "x", "definition": bad})
    assert r.status_code == 422 and "maskeret" in r.json()["detail"]
    # A field the catalog makes stricter later is masked harder at once (the policy is computed at run time).
    owner_conn.execute("update dataset_fields set classification = 'red' where dataset_id = %s and name = 'ejendom_id'", (ids["lejemaal"],))
    r = await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {}})
    assert r.status_code == 200 and r.json()["rows"][0]["ejendom_id"] == "•••"


async def test_cache_hit_encryption_clear_and_generation(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    op = await _op(admin, ids, cache={"ttl": 300})
    first = (await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"ejendom": "E-1"}})).json()
    assert first["cache"] == "miss"
    second = (await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"ejendom": "E-1"}})).json()
    assert second["cache"] == "hit" and second["rows"] == first["rows"]
    # Stored encrypted, and only masked: neither the address nor a name is readable in the table.
    blobs = [bytes(r[0]) for r in owner_conn.execute("select value from dev_cache")]
    assert blobs and all(b"E-1" not in b and b"Lejer" not in b for b in blobs)
    r = await admin.post(f"{DEV}/operations/{op['id']}/cache/clear")
    assert r.json()["generation"] == 1
    assert (await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"ejendom": "E-1"}})).json()["cache"] == "miss"
    # A new version uses a new key.
    r = await admin.put(f"{DEV}/operations/{op['id']}", json={"definition": {"source": {"dataset": ids["lejemaal"]},
                        "fields": ["id", "ejendom_id"], "params": [{"name": "ejendom", "field": "ejendom_id"}], "cache": {"ttl": 300}},
                        "submit": True})
    assert r.json()["version"]["changeType"] == "major"           # fields removed
    assert (await admin.post(f"{DEV}/operations/{op['id']}/try", json={"args": {"ejendom": "E-1"}})).json()["cache"] == "miss"
    assert (await admin.get(f"{DEV}/cache")).json()["backend"] == "postgres"


async def test_rate_limit_and_idempotency_in_postgres():
    results = [await cache.take("test:bucket", 3) for _ in range(4)]
    assert [ok for ok, _ in results] == [True, True, True, False]
    assert results[-1][1] >= 1
    assert await cache.idem_get("k1") is None
    assert await cache.idem_put("k1", 201, {"id": 7}) is True
    assert await cache.idem_put("k1", 201, {"id": 8}) is False
    assert await cache.idem_get("k1") == {"status": 201, "body": {"id": 7}}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def redis_url(monkeypatch):
    if not shutil.which("redis-server"):
        pytest.skip("redis-server findes ikke")
    port = _free_port()
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            break
        except OSError:
            import time
            time.sleep(0.05)
    monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{port}/0")
    config.reload()
    yield f"redis://127.0.0.1:{port}/0"
    proc.terminate()
    proc.wait(5)
    monkeypatch.delenv("REDIS_URL")
    config.reload()


async def test_redis_cache_rate_limit_and_idempotency(redis_url, owner_conn):
    assert cache.backend() == "redis"
    await cache.put("gc:test", {"rows": [1, 2]}, ttl_s=60)
    value, status, age = await cache.get("gc:test", ttl_s=60)
    assert value == {"rows": [1, 2]} and status == "hit"
    assert owner_conn.execute("select count(*) from dev_cache").fetchone()[0] == 0     # nothing in Postgres
    raw = await cache.redis().call("GET", "gc:test")
    assert b"rows" not in raw                                                       # encrypted in the app
    results = [await cache.take("redis:bucket", 2) for _ in range(3)]
    assert [ok for ok, _ in results] == [True, True, False]
    assert await cache.idem_put("r1", 200, {"a": 1}) is True and await cache.idem_put("r1", 200, {"a": 2}) is False
    assert (await cache.idem_get("r1"))["body"] == {"a": 1}


async def test_redis_down_falls_back_to_postgres(monkeypatch, owner_conn):
    monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{_free_port()}/0")
    config.reload()
    try:
        await cache.put("gc:fallback", {"x": 1}, ttl_s=60)
        value, status, _ = await cache.get("gc:fallback", ttl_s=60)
        assert value == {"x": 1} and status == "hit"
        assert (await cache.take("fb", 1))[0] is True
    finally:
        monkeypatch.delenv("REDIS_URL")
        config.reload()


async def test_stale_while_revalidate():
    await cache.put("gc:swr", {"v": 1}, ttl_s=1, max_stale_s=60)
    await asyncio.sleep(1.2)
    value, status, age = await cache.get("gc:swr", ttl_s=1, max_stale_s=60)
    assert status == "stale" and value == {"v": 1} and age >= 1


# ── Datastudie → /mcp/<slug> ────────────────────────────────────────────────

async def _guide(admin, ids, level="white"):
    r = await admin.post(f"{DEV}/datastudie/endpoints", json={"slug": "lejedata", "name": "Lejedata"})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert r.json()["url"].endswith("/mcp/lejedata")
    s = await admin.post(f"{DEV}/datastudie/sources", json={"description": "lejemaal og aarlig leje pr ejendom"})
    assert s.json()["items"][0]["name"] == "lejemaal"
    sample = await admin.post(f"{DEV}/datastudie/sample", json={"dataset_id": ids["lejemaal"], "fields": ["ejendom_id", "aarlig_leje", "lejer_navn"]})
    assert sample.status_code == 200, sample.text
    sug = {x["field"]: x for x in sample.json()["suggestions"]}
    assert sug["lejer_navn"]["suggested"] == "mask" and sug["aarlig_leje"]["suggested"] == "interval"
    assert all(r["lejer_navn"] == "•••" for r in sample.json()["rows"])
    g = await admin.post(f"{DEV}/datastudie/endpoints/{eid}/generate", json={
        "description": "Lejemål og leje", "instructions": "Svar kort.",
        "sources": [{"dataset_id": ids["lejemaal"], "fields": ["id", "ejendom_id", "aarlig_leje", "lejer_navn"],
                     "params": [{"name": "ejendom", "field": "ejendom_id"}], "tool": "soeg_lejemaal",
                     "description": "Søg lejemål på ejendom. Returnerer leje som interval.", "level": level}]})
    assert g.status_code == 200, g.text
    r = await admin.post(f"{DEV}/artifacts/{eid}/submit", json={})
    assert r.json()["status"] == "published", r.text
    return eid


async def test_guide_publishes_endpoint_with_operation_tools(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    eid = await _guide(admin, ids)
    detail = (await admin.get(f"{DEV}/datastudie/endpoints/{eid}")).json()
    assert detail["live"] and [t["name"] for t in detail["tools"]] == ["soeg_lejemaal"]
    assert detail["tools"][0]["fieldLevel"] == "red"
    # An internal user connects to /mcp/lejedata and calls the tool: masked, and the call is logged with masked args.
    user = make_client()
    await otp_login(user, "20000009")
    owner_conn.execute("update users set audience = 'internal' where phone = '+4520000009'")
    t = await mcp_token(user, "mcp")
    m = await McpClient(user, "/mcp/lejedata", t["access_token"]).initialize()
    names = {x["name"] for x in await m.list_tools()}
    assert "soeg_lejemaal" in names and "whoami" in names
    result, is_error = await m.call("soeg_lejemaal", ejendom="E-1")
    assert not is_error, result
    assert result["count"] == 12 and {r["lejer_navn"] for r in result["rows"]} == {"•••"}
    log = owner_conn.execute("select args, status, cache from mcp_log where server = 'lejedata' and tool = 'soeg_lejemaal'").fetchone()
    assert log[1] == "ok" and log[0] == {"ejendom": "E-1"}       # ejendom_id is white: logged as is
    from app.devkit import operations
    green = {"_output": [{"name": "adresse", "classification": "green", "personal": False}],
             "params": [{"name": "adresse", "field": "adresse"}]}
    assert operations.sensitive_params(green) == {"adresse"}          # other levels are masked in the log (MCP-14)
    # MCP Studio cannot change an endpoint that Datastudie owns.
    r = await admin.patch("/api/admin/studio/mcp-endpoints/lejedata", json={"name": "x"})
    assert r.status_code in (403, 409)
    # Disabling the artifact disables the endpoint.
    await admin.patch(f"{DEV}/artifacts/{eid}", json={"enabled": False})
    assert owner_conn.execute("select enabled from mcp_endpoints where key = 'lejedata'").fetchone()[0] is False


async def test_levels_and_external_users(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    await _guide(admin, ids, level="yellow")
    internal = make_client()
    await otp_login(internal, "20000010")
    owner_conn.execute("update users set audience = 'internal' where phone = '+4520000010'")
    m = await McpClient(internal, "/mcp/lejedata", (await mcp_token(internal, "mcp"))["access_token"]).initialize()
    tools = {x["name"]: x for x in await m.list_tools()}
    assert "Låst" in tools["soeg_lejemaal"]["description"]
    result, is_error = await m.call("soeg_lejemaal")
    assert is_error and "adgang" in result["error"]
    # Endpoint audience is internal: an external user cannot connect at all; with audience all, they see white tools only.
    owner_conn.execute("update mcp_endpoints set audience = 'all' where key = 'lejedata'")
    external = make_client()
    await otp_login(external, "20000011")
    m2 = await McpClient(external, "/mcp/lejedata", (await mcp_token(external, "mcp"))["access_token"]).initialize()
    assert "soeg_lejemaal" not in {x["name"] for x in await m2.list_tools()}


async def test_major_in_operation_flags_endpoint(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    eid = await _guide(admin, ids)
    op = (await admin.get(f"{DEV}/operations")).json()["items"][0]
    assert op["usedBy"] == [{"tool": "soeg_lejemaal", "endpoint": "lejedata"}]
    r = await admin.put(f"{DEV}/operations/{op['id']}", json={"definition": {"source": {"dataset": ids["lejemaal"]}, "fields": ["id"]}, "submit": True})
    assert r.json()["version"]["changeType"] == "major"
    assert (await admin.get(f"{DEV}/artifacts/{eid}")).json()["status"] == "review_required"
    overview = (await admin.get(f"{DEV}/overview")).json()
    assert overview["counts"]["reviews"] == 1


async def test_tool_description_check_without_ai(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    r = await admin.post(f"{DEV}/datastudie/check-tool", json={"tool": {"name": "x", "description": "kort"}})
    assert r.json()["ok"] is False and r.json()["ai"] is False and len(r.json()["problems"]) == 2
    assert os.environ.get("REDIS_URL") is None
