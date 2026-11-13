"""Gær Developer, fase 2: pipelines (PL-1 … PL-17), snapshots in `snapshots`, runs and the plan, and connections
through FDW or directly (F-11, F-12, PL-5)."""

import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pytest

from app import jobs
from app.devkit import pipeline_runs, pipelines
from tests.helpers import full_user, stepup

DEV = "/api/admin/dev"

SPEC = """
name: lejemaal_pr_ejendom
schedule: dagligt 06:00
wait_for_load: false
key: ejendom_id
stages:
  - source: lejemaal
  - match:
      status: aktiv
  - lookup:
      from: ejendomme
      local: ejendom_id
      foreign: id
      fields: [adresse]
  - group:
      by: ejendom_id
      antal_lejemaal: count
      aarlig_leje_i_alt: sum(aarlig_leje)
"""


def _tables(owner_conn, small: bool = False):
    owner_conn.execute("""
        drop table if exists lejemaal, ejendomme;
        create table ejendomme (id text primary key, adresse text);
        create table lejemaal (id int primary key, ejendom_id text, status text, aarlig_leje numeric, lejer_navn text);
        select gaer_apply_grants();""")
    owner_conn.execute("insert into ejendomme values ('E-1', 'Eksempelvej 1'), ('E-2', 'Testgade 2')")
    rows = []
    for i in range(1, 13):
        rows.append((i, "E-1", "aktiv", 100000 + i * 1000, f"Lejer {i}"))
    for i in range(13, 16 if small else 21):
        rows.append((i, "E-2", "aktiv" if i % 2 else "ophørt", 90000, f"Lejer {i}"))
    for r in rows:
        owner_conn.execute("insert into lejemaal values (%s, %s, %s, %s, %s)", r)


async def _setup(make_client, owner_conn, small=False):
    admin = make_client()
    auth = await full_user(admin, "20000001")
    owner_conn.execute("update users set audience = 'internal'")
    _tables(owner_conn, small)
    ids = {}
    for t in ("lejemaal", "ejendomme"):
        r = await admin.post("/api/admin/data/datasets", json={"table": t})
        assert r.status_code == 201, r.text
        ids[t] = r.json()["id"]
    owner_conn.execute("update dataset_fields set classification = 'white' where name in ('id', 'ejendom_id', 'status')")
    owner_conn.execute("update dataset_fields set classification = 'yellow' where name = 'aarlig_leje'")
    owner_conn.execute("update dataset_fields set classification = 'red', personal_data = true where name = 'lejer_navn'")
    owner_conn.execute("update dataset_fields set classification = 'green' where name = 'adresse'")
    return admin, auth, ids


async def _stepup_call(client, auth, method, url, body=None):
    send = getattr(client, method)
    r = await (send(url, json=body) if body is not None else send(url))
    if r.status_code == 403 and r.headers.get("x-gaer-error") == "stepup_required":
        await stepup(client, auth)
        r = await (send(url, json=body) if body is not None else send(url))
    return r


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_parse_and_yaml_roundtrip():
    d = pipelines.parse(SPEC)
    assert d["schedule"] == {"type": "daily", "earliest": "06:00", "latest": "08:00"}
    assert [next(iter(s)) for s in d["stages"]] == ["source", "match", "lookup", "group"]
    again = pipelines.parse(pipelines.to_yaml(d))
    assert again == d
    with pytest.raises(Exception, match="sænkes"):
        pipelines.parse(SPEC + "min_group_size: 3\n")
    with pytest.raises(Exception, match="Første trin"):
        pipelines.parse({"stages": [{"match": {"a": 1}}]})
    with pytest.raises(Exception, match="ukendt trin"):
        pipelines.parse({"stages": [{"source": "x"}, {"drop": {}}]})
    assert pipelines.parse({"stages": [{"source": "x"}], "schedule": "hver time"})["schedule"] == {"type": "hourly"}


# ── Compiling: SQL, lineage, classification ────────────────────────────────

async def test_compile_lineage_and_inherited_classification(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    from app import db
    async with db.connection() as conn:
        c = await pipelines.compile_definition(conn, pipelines.parse(SPEC))
    out = {x.name: x for x in c.columns}
    assert list(out) == ["ejendom_id", "antal_lejemaal", "aarlig_leje_i_alt"]
    assert out["aarlig_leje_i_alt"].classification == "yellow" and out["aarlig_leje_i_alt"].transform == "sum(aarlig_leje)"
    assert out["antal_lejemaal"].classification == "white"
    assert [label for _, label in out["aarlig_leje_i_alt"].sources] == ["lejemaal.aarlig_leje"]
    # A computed field inherits the strictest of its sources and the personal-data mark (F-9).
    d = pipelines.parse({"stages": [{"source": "lejemaal"}, {"addFields": {"tekst": {"$concat": ["$lejer_navn", " · ", "$status"]}}}]})
    async with db.connection() as conn:
        c2 = await pipelines.compile_definition(conn, d)
    tekst = c2.columns[-1]
    assert tekst.classification == "red" and tekst.personal
    # Values are literals and names must exist: a definition cannot inject SQL.
    evil = pipelines.parse({"stages": [{"source": "lejemaal"}, {"match": {"status": "aktiv'; drop table lejemaal; --"}}]})
    async with db.connection() as conn:
        c3 = await pipelines.compile_definition(conn, evil)
    assert (await pipelines.preview(c3))["rows"] == []
    assert owner_conn.execute("select count(*) from lejemaal").fetchone()[0] > 0
    with pytest.raises(Exception, match="findes ikke"):
        async with db.connection() as conn:
            await pipelines.compile_definition(conn, pipelines.parse({"stages": [{"source": "lejemaal"}, {"match": {"x; drop": 1}}]}))


async def test_preview_is_masked_and_shows_sql(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    r = await admin.post(f"{DEV}/pipelines/preview", json={"definition": SPEC, "stage": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    row = body["rows"][0]
    assert row["lejer_navn"] == "•••" and "-" in str(row["aarlig_leje"])
    assert body["sql"].lower().startswith("with") and "lejemaal" in body["sql"]
    r = await admin.post(f"{DEV}/pipelines/preview", json={"definition": SPEC})
    by_id = {x["ejendom_id"]: x for x in r.json()["rows"]}
    assert by_id["E-1"]["antal_lejemaal"] == 12
    assert r.json()["warnings"] == []          # lookup from the same database: no cross-source warning
    fmt = await admin.post(f"{DEV}/pipelines/format", json={"yaml": SPEC})
    assert fmt.json()["definition"]["key"] == "ejendom_id" and "stages:" in fmt.json()["yaml"]


async def test_run_builds_snapshot_registers_catalog_and_refreshes(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    r = await admin.post(f"{DEV}/pipelines", json={"slug": "lejemaal_pr_ejendom", "name": "Lejemål pr. ejendom", "definition": SPEC})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (await admin.post(f"{DEV}/pipelines/{pid}/run")).status_code == 422          # nothing published yet
    r = await admin.post(f"{DEV}/artifacts/{pid}/submit", json={})
    assert r.json()["status"] == "published"
    r = await admin.post(f"{DEV}/pipelines/{pid}/run")
    assert r.status_code == 200, r.text
    assert (await admin.post(f"{DEV}/pipelines/{pid}/run")).status_code == 422           # already running
    await jobs.run_pending()
    run = (await admin.get(f"{DEV}/runs/{r.json()['runId']}")).json()
    assert run["status"] == "succeeded", run
    assert run["rows"] == 2 and [p["phase"] for p in run["phases"]] == ["wait", "compile", "build", "catalog"]
    assert owner_conn.execute("select count(*) from snapshots.lejemaal_pr_ejendom").fetchone()[0] == 2
    snap = owner_conn.execute("select id, kind, generation from datasets where key = 'snapshots.lejemaal_pr_ejendom'").fetchone()
    assert snap[1] == "snapshot" and snap[2] == 1
    fields = {f[0]: f[1:] for f in owner_conn.execute(
        "select name, classification, personal_data, inherited from dataset_fields where dataset_id = %s", (snap[0],))}
    assert fields["aarlig_leje_i_alt"] == ("yellow", False, True)
    detail = (await admin.get(f"{DEV}/catalog/datasets/{snap[0]}")).json()
    assert any(l["field"] == "aarlig_leje_i_alt" and l["source_label"] == "lejemaal.aarlig_leje" for l in detail["lineage"])
    # Udforsk reads the snapshot; yellow is masked there too.
    q = await admin.post(f"/api/admin/data/datasets/{snap[0]}/query", json={})
    assert q.status_code == 200, q.text and len(q.json()["rows"]) == 2

    # Same structure: REFRESH CONCURRENTLY.
    owner_conn.execute("insert into lejemaal values (100, 'E-1', 'aktiv', 1000, 'Ny')")
    rid = (await admin.post(f"{DEV}/pipelines/{pid}/run")).json()["runId"]
    await jobs.run_pending()
    run = (await admin.get(f"{DEV}/runs/{rid}")).json()
    assert [p["phase"] for p in run["phases"]][2] == "refresh" and run["status"] == "succeeded"

    # A new field is a minor; the next run builds the new structure next to the old and swaps.
    new = SPEC.replace("aarlig_leje_i_alt: sum(aarlig_leje)", "aarlig_leje_i_alt: sum(aarlig_leje)\n      hoejeste: max(aarlig_leje)")
    r = await admin.put(f"{DEV}/pipelines/{pid}", json={"definition": new})
    assert r.json()["version"]["changeType"] == "minor"
    await admin.post(f"{DEV}/artifacts/{pid}/submit", json={})
    rid = (await admin.post(f"{DEV}/pipelines/{pid}/run")).json()["runId"]
    await jobs.run_pending()
    run = (await admin.get(f"{DEV}/runs/{rid}")).json()
    assert run["phases"][2]["detail"].startswith("ny struktur")
    assert owner_conn.execute("select hoejeste from snapshots.lejemaal_pr_ejendom where ejendom_id = 'E-1'").fetchone()[0] is not None
    snaps = (await admin.get(f"{DEV}/snapshots")).json()["items"]
    assert snaps[0]["version"] == "1.1.0" and snaps[0]["rows"] == 2


async def test_failed_run_keeps_last_snapshot_and_tells_responsible(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    pid = (await admin.post(f"{DEV}/pipelines", json={"slug": "p1", "name": "P1", "definition": SPEC})).json()["id"]
    await admin.post(f"{DEV}/artifacts/{pid}/submit", json={})
    await admin.post(f"{DEV}/pipelines/{pid}/run")
    await jobs.run_pending()
    owner_conn.execute("alter table lejemaal rename column aarlig_leje to leje")
    rid = (await admin.post(f"{DEV}/pipelines/{pid}/run")).json()["runId"]
    await jobs.run_pending()
    run = (await admin.get(f"{DEV}/runs/{rid}")).json()
    assert run["status"] == "failed" and "Databasefejl" in run["error"]
    assert owner_conn.execute("select count(*) from snapshots.p1").fetchone()[0] == 2
    notes = (await admin.get(f"{DEV}/notifications")).json()["items"]
    assert any(n["kind"] == "pipeline_failed" for n in notes)


async def test_small_groups_block_publishing_personal_output(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn, small=True)
    spec = SPEC.replace("aarlig_leje_i_alt: sum(aarlig_leje)", "aarlig_leje_i_alt: sum(aarlig_leje)\n      en_lejer: max(lejer_navn)")
    r = await admin.post(f"{DEV}/pipelines/preview", json={"definition": spec})
    assert r.json()["smallGroups"] == 1 and r.json()["sensitive"] == ["en_lejer"]
    pid = (await admin.post(f"{DEV}/pipelines", json={"slug": "p2", "name": "P2", "definition": spec})).json()["id"]
    r = await admin.post(f"{DEV}/artifacts/{pid}/submit", json={})
    assert r.status_code == 409 and "færre end 5" in r.json()["detail"]
    # Without the personal field the same small group is only a warning.
    pid2 = (await admin.post(f"{DEV}/pipelines", json={"slug": "p3", "name": "P3", "definition": SPEC})).json()["id"]
    assert (await admin.post(f"{DEV}/artifacts/{pid2}/submit", json={})).json()["status"] == "published"


async def test_dependencies_order_and_cycles(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    a = (await admin.post(f"{DEV}/pipelines", json={"slug": "basis", "name": "Basis", "definition": SPEC})).json()["id"]
    await admin.post(f"{DEV}/artifacts/{a}/submit", json={})
    await admin.post(f"{DEV}/pipelines/{a}/run")
    await jobs.run_pending()
    b_spec = {"key": "ejendom_id", "stages": [{"source": "snapshots.basis"}, {"match": {"antal_lejemaal": {"$gte": 5}}}]}
    b = (await admin.post(f"{DEV}/pipelines", json={"slug": "store", "name": "Store", "definition": b_spec})).json()["id"]
    await admin.post(f"{DEV}/artifacts/{b}/submit", json={})
    detail = (await admin.get(f"{DEV}/pipelines/{b}")).json()
    assert [d["slug"] for d in detail["dependsOn"]] == ["basis"]
    await admin.post(f"{DEV}/pipelines/{b}/run")
    await jobs.run_pending()
    # basis may not start reading store: that is a cycle.
    cyc = {"key": "id", "stages": [{"source": "lejemaal"}, {"lookup": {"from": "snapshots.store", "local": "ejendom_id",
                                                                         "foreign": "ejendom_id", "fields": ["antal_lejemaal"]}}]}
    r = await admin.put(f"{DEV}/pipelines/{a}", json={"definition": cyc})
    assert r.status_code == 422 and "cirkulær" in r.json()["detail"]


async def test_plan_waits_for_load_then_defers_and_runs(make_client, owner_conn, monkeypatch):
    admin, auth, ids = await _setup(make_client, owner_conn)
    spec = SPEC.replace("wait_for_load: false", "wait_for_load: true\nload_sources: [mysql_backup_1]")
    pid = (await admin.post(f"{DEV}/pipelines", json={"slug": "plan", "name": "Plan", "definition": spec})).json()["id"]
    await admin.post(f"{DEV}/artifacts/{pid}/submit", json={})
    today = pipelines.now_local().date()
    clock = {"t": pipelines.at(today, "05:30")}
    monkeypatch.setattr(pipelines, "now_local", lambda: clock["t"])
    assert (await jobs.HANDLERS["pipeline.schedule"]({}, {}))["started"] == []          # before 06:00
    clock["t"] = pipelines.at(today, "07:00")
    assert (await jobs.HANDLERS["pipeline.schedule"]({}, {}))["started"] == []          # load not done
    clock["t"] = pipelines.at(today, "08:05")
    out = await jobs.HANDLERS["pipeline.schedule"]({}, {})
    assert len(out["deferred"]) == 1
    assert (await jobs.HANDLERS["pipeline.schedule"]({}, {}))["deferred"] == []          # once a day
    runs = (await admin.get(f"{DEV}/runs?artifact_id={pid}")).json()["items"]
    assert runs[0]["status"] == "deferred" and "mysql_backup_1" in runs[0]["error"]
    # The load comes later the same day: the run starts by itself.
    owner_conn.execute("insert into ingest.load_status (source, load_date, status, finished_at) values ('mysql_backup_1', %s, 'succeeded', now())",
                       (today,))
    clock["t"] = pipelines.at(today, "09:10")
    out = await jobs.HANDLERS["pipeline.schedule"]({}, {})
    assert len(out["started"]) == 1
    await jobs.run_pending()
    assert (await admin.get(f"{DEV}/runs?artifact_id={pid}")).json()["items"][0]["status"] == "succeeded"
    assert (await jobs.HANDLERS["pipeline.schedule"]({}, {}))["started"] == []          # done for today


# ── Connections ─────────────────────────────────────────────────────────────

def _remote(owner_conn):
    owner_conn.execute("""drop schema if exists remote_src cascade; create schema remote_src;
                          create table remote_src.lejere (id int primary key, navn text, email text, password_hash text);
                          insert into remote_src.lejere values (1, 'Jens', 'j@x.dk', 'x'), (2, 'Mette', 'm@x.dk', 'y');""")
    from tests import conftest
    u = urlparse(conftest.ADMIN_URL)
    os.environ["DATA_SECRET_PG_TEST"] = u.password or ""
    return {"type": "postgres", "host": u.hostname, "port": u.port or 5432, "database": conftest.DB_NAME, "user": u.username,
            "password": "DATA_SECRET_PG_TEST", "schema": "remote_src", "tables": ["lejere"], "ingest_source": "crm"}


async def test_connection_fdw_catalog_and_pipeline(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    caps = (await admin.get(f"{DEV}/connections/capabilities")).json()
    if not caps["postgres_fdw"]:
        pytest.skip("postgres_fdw er ikke installeret på testserveren")
    definition = _remote(owner_conn)
    r = await admin.post(f"{DEV}/connections", json={"key": "crm", "name": "CRM", "definition": {**definition, "access": "fdw"}})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["artifact"]["version"] == "1.0.0"
    r = await admin.post(f"{DEV}/connections/{cid}/sync")
    assert r.status_code == 200, r.text
    ds = r.json()["datasets"][0]
    names = [f[0] for f in owner_conn.execute("select name from dataset_fields where dataset_id = %s order by position", (ds,))]
    assert names == ["id", "navn", "email"]             # password_hash is left out of the catalog
    owner_conn.execute("update dataset_fields set classification = 'white' where dataset_id = %s and name = 'id'", (ds,))
    q = await admin.post(f"/api/admin/data/datasets/{ds}/query", json={})
    assert q.status_code == 200, q.text
    assert {row["id"] for row in q.json()["rows"]} == {1, 2} and {row["navn"] for row in q.json()["rows"]} == {"•••"}
    # A pipeline over the foreign table, joined in PostgreSQL; a cross-source lookup without a filter warns.
    spec = {"key": "id", "stages": [{"source": "crm.lejere"}, {"lookup": {"from": "lejemaal", "local": "id", "foreign": "id", "fields": ["status"]}}]}
    r = await admin.post(f"{DEV}/pipelines/preview", json={"definition": spec})
    assert r.status_code == 200, r.text
    assert len(r.json()["rows"]) == 2 and r.json()["warnings"]
    listed = (await admin.get(f"{DEV}/connections")).json()["items"]
    assert listed[0]["access"] == "FDW" and listed[0]["status"] == "ok"
    # A plain Udvikler cannot create or change connections.
    dev = make_client()
    dev_auth = await full_user(dev, "20000002")
    owner_conn.execute("update users set role = 'admin', admin_roles = '{udvikler}', audience = 'internal' where phone = '+4520000002'")
    r = await _stepup_call(dev, dev_auth, "post", f"{DEV}/connections/{cid}/sync")
    assert r.status_code == 403
    assert (await _stepup_call(dev, dev_auth, "get", f"{DEV}/connections/{cid}")).status_code == 200


async def test_connection_direct_mysql_and_delegated(make_client, owner_conn):
    admin, auth, ids = await _setup(make_client, owner_conn)
    definition = _remote(owner_conn)
    r = await admin.post(f"{DEV}/connections", json={"key": "crmdirekte", "name": "CRM direkte", "definition": {**definition, "access": "direct"}})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    t = await admin.post(f"{DEV}/connections/{cid}/test")
    assert t.status_code == 200, t.text and "lejere" in t.json()["tables"]
    s = await admin.post(f"{DEV}/connections/{cid}/sync")
    assert s.status_code == 200 and len(s.json()["datasets"]) == 1
    # Secrets are never written into a definition.
    bad = await admin.post(f"{DEV}/connections", json={"key": "x", "name": "x", "definition": {**definition, "password": "hemmeligt123"}})
    assert bad.status_code == 422
    # MySQL without mysql_fdw says so clearly.
    my = await admin.post(f"{DEV}/connections", json={"key": "mysql1", "name": "MySQL backup 1",
                                                       "definition": {"type": "mysql", "host": "db", "database": "lejedata", "user": "l", "tables": ["lejemaal"]}})
    assert my.status_code == 201
    assert "mysql_fdw" in (my.json()["problem"] or "")
    r = await admin.post(f"{DEV}/connections/{my.json()['id']}/test")
    assert r.status_code == 409 and "mysql_fdw" in r.json()["detail"]
    # Delegated access is an extension point: it exists, and answers that it is not available yet.
    de = await admin.post(f"{DEV}/connections", json={"key": "m365", "name": "Microsoft 365", "definition": {"type": "delegated", "provider": "microsoft"}})
    assert de.status_code == 201 and "ikke tilgængelig endnu" in de.json()["problem"]
    r = await admin.post(f"{DEV}/connections/{de.json()['id']}/sync")
    assert r.status_code == 409


async def test_next_run_and_hourly():
    d = pipelines.parse({"stages": [{"source": "x"}], "schedule": "dagligt 06:00"})
    nr = pipelines.next_run(d, None)
    assert nr is not None
    assert pipelines.next_run({**d, "paused": True}, None) is None
    last = pipelines.now_local()
    assert pipelines.next_run(d, last).date() == (last + timedelta(days=1)).date()
    h = pipelines.parse({"stages": [{"source": "x"}], "schedule": "hver time"})
    assert pipelines.next_run(h, last) == last + timedelta(hours=1)
    assert isinstance(pipeline_runs.RUN_DA["deferred"], str) and isinstance(datetime.now(), datetime)
