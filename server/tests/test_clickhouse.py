"""ClickHouse: the HTTP client (app.clickhouse), its schema runner, /admin/clickhouse, the retention policies
clickhouse_* and the job clickhouse.monitor. ClickHouse itself is replaced by a fake behind an httpx.MockTransport."""

import dataclasses
import json

import httpx
import pytest

from app import admin_roles, clickhouse, clickhouse_migrate, clickhouse_monitor, config, migrate, retention
from tests.helpers import full_user, otp_login

UNKNOWN_TABLE = "Code: 60. DB::Exception: Table system.part_log does not exist. (UNKNOWN_TABLE) (version 26.8.36.3 (official build))"


class FakeClickHouse:
    """Answers the fixed queries the app sends. Settings on the instance change what it reports."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.down = False
        self.free_share = 0.6
        self.max_parts = 12
        self.failing_mutations = 0
        self.applied = ["0001_events.sql"]
        self.old_rows = {"events": 0, "system.query_log": 0, "system.part_log": 0}
        self.missing = set()
        self.reject_alter = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused")
        if request.url.path == "/ping":
            return httpx.Response(200, text="Ok.\n")
        sql = request.url.params.get("query", "")

        def rows(data):
            return httpx.Response(200, json={"meta": [], "data": data, "rows": len(data)})

        for table in self.missing:
            if f"FROM {table}" in sql or f"TABLE {table}" in sql:
                return httpx.Response(404, text=UNKNOWN_TABLE.replace("system.part_log", table))
        if "version()" in sql:
            return rows([{"version": "26.8.36.3", "uptime_seconds": 90061, "timezone": "UTC", "database": "gaer"}])
        if "FROM system.disks" in sql:
            total = 100_000_000_000
            return rows([{"name": "default", "path": "/var/lib/clickhouse/", "free_space": int(total * self.free_share),
                          "total_space": total}])
        if "FROM system.metrics" in sql:
            return rows([{"metric": "Query", "value": 1}, {"metric": "HTTPConnection", "value": 2},
                         {"metric": "MemoryTracking", "value": 512_000_000}])
        if "FROM system.asynchronous_metrics" in sql:
            return rows([{"metric": "MaxPartCountForPartition", "value": self.max_parts},
                         {"metric": "OSMemoryTotal", "value": 8_000_000_000}, {"metric": "NumberOfTables", "value": 90}])
        if "FROM system.mutations WHERE NOT is_done" in sql:
            return rows([{"failing": self.failing_mutations, "stuck": 0}])
        if "FROM system.parts" in sql:
            return rows([{"database": "gaer", "table": "events", "parts": 3, "rows": 1200, "bytes": 40960,
                          "modified": "2026-11-09 10:00:00"}])
        if "FROM system.processes" in sql:
            return rows([{"query_id": "q1", "user": "gaer", "elapsed_seconds": 0.4, "read_rows": 10,
                          "memory_usage": 4096, "query": "SELECT 1"}])
        if "FROM schema_migrations" in sql:
            return rows([{"name": n} for n in self.applied])
        if sql.startswith("SELECT count() AS n FROM "):
            table = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return rows([{"n": self.old_rows.get(table, 0)}])
        if sql.startswith("ALTER TABLE"):
            if self.reject_alter:
                return httpx.Response(500, text="Code: 241. DB::Exception: Memory limit exceeded. (MEMORY_LIMIT_EXCEEDED)")
            return httpx.Response(200, text="")
        if sql.startswith(("CREATE", "INSERT")):
            return httpx.Response(200, text="")
        return httpx.Response(400, text=f"Code: 62. DB::Exception: Syntax error: unexpected query in fake: {sql[:60]}")

    def sql(self) -> list[str]:
        return [r.url.params.get("query", "") for r in self.requests if r.url.path != "/ping"]


@pytest.fixture
def ch(monkeypatch, owner_conn):
    owner_conn.execute("truncate clickhouse_checks restart identity")
    monkeypatch.setattr(config, "settings", dataclasses.replace(
        config.settings, clickhouse_url="http://clickhouse:8123", clickhouse_user="gaer",
        clickhouse_password="s3cret-pass", clickhouse_database="gaer"))
    fake = FakeClickHouse()
    transport = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(clickhouse, "transport", transport)
    monkeypatch.setattr(clickhouse, "sync_transport", transport)
    return fake


# ── The client ──────────────────────────────────────────────────────────────

async def test_values_go_as_parameters_and_credentials_as_headers(ch):
    rows = await clickhouse.query("SELECT count() AS n FROM events WHERE kind = {kind:String}", {"kind": "x'; DROP"})
    assert rows == [{"n": 0}]
    req = ch.requests[-1]
    assert req.url.params["param_kind"] == "x'; DROP" and "DROP" not in req.url.params["query"]
    assert req.url.params["query"].endswith("FORMAT JSON")
    assert req.url.params["database"] == "gaer"
    assert req.headers["X-ClickHouse-User"] == "gaer" and req.headers["X-ClickHouse-Key"] == "s3cret-pass"
    assert "s3cret-pass" not in str(req.url)


async def test_insert_sends_json_each_row_and_checks_the_table_name(ch):
    assert await clickhouse.insert("events", [{"kind": "clip.saved", "ref": "clip:12"}, {"kind": "clip.saved"}]) == 2
    req = ch.requests[-1]
    assert req.url.params["query"] == "INSERT INTO events FORMAT JSONEachRow"
    assert [json.loads(line) for line in req.content.decode().splitlines()][0] == {"kind": "clip.saved", "ref": "clip:12"}
    with pytest.raises(ValueError):
        await clickhouse.insert("events; DROP TABLE events", [{"kind": "x"}])
    assert await clickhouse.insert("events", []) == 0


async def test_errors_are_short_and_carry_the_code(ch):
    ch.missing.add("events")
    with pytest.raises(clickhouse.ClickHouseError) as e:
        await clickhouse.query("SELECT count() AS n FROM events")
    assert e.value.code == 60 and e.value.status == 400
    assert e.value.message == "ClickHouse: Table events does not exist. (UNKNOWN_TABLE)"
    ch.down = True
    with pytest.raises(clickhouse.ClickHouseError) as e:
        await clickhouse.ping()
    assert e.value.status == 502 and e.value.code is None


async def test_off_without_url(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, clickhouse_url=None))
    assert clickhouse.configured() is False
    with pytest.raises(clickhouse.ClickHouseError) as e:
        await clickhouse.query("SELECT 1")
    assert e.value.status == 503
    assert clickhouse_migrate.migrate(verbose=False) == []


def test_redact_url():
    assert clickhouse.redact_url("http://gaer:pw@clickhouse:8123") == "http://••••@clickhouse:8123"
    assert clickhouse.redact_url("http://clickhouse:8123") == "http://clickhouse:8123"
    assert clickhouse.redact_url(None) is None


# ── Schema ──────────────────────────────────────────────────────────────────

def test_statements_split_on_semicolon_at_line_end():
    text = "-- comment\nCREATE TABLE a (x String DEFAULT ';');\n\n-- only a comment;\nCREATE TABLE b (y UInt8);\n"
    assert clickhouse_migrate.statements(text) == ["CREATE TABLE a (x String DEFAULT ';')", "CREATE TABLE b (y UInt8)"]
    events = clickhouse_migrate.statements((clickhouse_migrate.MIGRATIONS_DIR / "0001_events.sql").read_text())
    assert len(events) == 1 and events[0].startswith("CREATE TABLE IF NOT EXISTS events")


def test_migrate_creates_database_and_records_files(ch):
    ch.applied = []
    assert clickhouse_migrate.migrate(verbose=False) == ["0001_events.sql"]
    sql = ch.sql()
    create_db = next(r for r in ch.requests if r.url.params.get("query", "").startswith("CREATE DATABASE"))
    assert create_db.url.params["query"] == "CREATE DATABASE IF NOT EXISTS gaer" and create_db.url.params["database"] == "system"
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS schema_migrations") for s in sql)
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS events") for s in sql)
    insert = next(r for r in ch.requests if r.url.params.get("query", "").startswith("INSERT INTO schema_migrations"))
    assert insert.url.params["param_name"] == "0001_events.sql"

    ch.applied = ["0001_events.sql"]
    ch.requests.clear()
    assert clickhouse_migrate.migrate(verbose=False) == []
    assert not any("events" in s and s.startswith("CREATE TABLE") for s in ch.sql())


def test_app_migrate_warns_but_does_not_fail_when_clickhouse_is_down(ch, capsys):
    ch.down = True
    migrate.migrate(verbose=False)  # Postgres is up to date; ClickHouse does not answer
    assert "clickhouse migrations not applied" in capsys.readouterr().err


# ── /admin/clickhouse ───────────────────────────────────────────────────────

async def test_not_configured_says_so(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, clickhouse_url=None))
    await full_user(client, "11111111")
    body = (await client.get("/api/admin/clickhouse")).json()
    assert body["configured"] is False and body["reachable"] is False and body["history"] == []
    r = await client.get("/api/admin/clickhouse/tables")
    assert r.status_code == 503 and "ikke sat op" in r.json()["detail"]


async def test_overview(client, ch, owner_conn):
    await full_user(client, "11111111")
    r = await client.get("/api/admin/clickhouse")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["error"] is None
    assert body["server"]["version"] == "26.8.36.3" and body["server"]["uptime"] == "1 d 1 t"
    assert body["metrics"]["MemoryTracking"] == 512_000_000 and body["metrics"]["MaxPartCountForPartition"] == 12
    assert body["tables"][0]["table"] == "events" and body["pending_migrations"] == []
    assert body["user"] == "gaer" and "s3cret-pass" not in r.text
    assert {v["key"] for v in body["views"]} == set(clickhouse_monitor.VIEWS)
    row = owner_conn.execute("select detail->>'path' from audit_log where action = 'admin.read' order by id desc limit 1").fetchone()
    assert row == ("/api/admin/clickhouse",)

    ch.down = True
    body = (await client.get("/api/admin/clickhouse")).json()
    assert body["reachable"] is False and "svarer ikke" in body["error"]


async def test_views_are_fixed_queries(client, ch):
    await full_user(client, "11111111")
    r = await client.get("/api/admin/clickhouse/queries")
    assert r.status_code == 200 and r.json()["rows"][0]["query_id"] == "q1"
    assert "FROM system.processes" in ch.sql()[-1]
    assert (await client.get("/api/admin/clickhouse/drop_everything")).status_code == 404
    assert (await client.get("/api/admin/clickhouse/tables?query=DROP%20TABLE%20events")).json()["view"] == "tables"
    assert not any("DROP" in s for s in ch.sql())
    r = await client.get("/api/admin/clickhouse/checks")
    assert r.status_code == 200 and r.json()["rows"] == []


async def test_only_admins_with_drift_or_full_access(make_client, ch):
    boss = make_client()
    await full_user(boss, "11111111")
    other = make_client()
    await full_user(other, "22222222")
    other_id = (await boss.get("/api/admin/users")).json()["items"][1]["id"]

    assert (await other.get("/api/admin/clickhouse")).status_code == 403             # not admin
    assert (await boss.patch(f"/api/admin/users/{other_id}/role", json={"role": "admin"})).status_code == 200
    assert (await boss.put(f"/api/admin/users/{other_id}/admin-roles", json={"roles": ["support"]})).status_code == 200
    assert (await other.get("/api/admin/clickhouse")).status_code == 403
    assert (await boss.put(f"/api/admin/users/{other_id}/admin-roles", json={"roles": ["drift"]})).status_code == 200
    assert (await other.get("/api/admin/clickhouse")).status_code == 200
    assert "/clickhouse" in admin_roles.sections(["drift"])


async def test_plain_user_cannot_read(make_client, ch):
    boss = make_client()
    await full_user(boss, "11111111")
    user = make_client()
    await otp_login(user, "22222222")
    assert (await user.get("/api/admin/clickhouse/tables")).status_code == 403


# ── The monitor ─────────────────────────────────────────────────────────────

async def test_check_thresholds(ch):
    result = await clickhouse_monitor.check()
    assert result["status"] == "ok" and result["version"] == "26.8.36.3"
    assert {c["key"] for c in result["checks"]} == {"reachable", "disk", "parts", "mutations", "migrations"}

    ch.free_share = 0.15
    ch.max_parts = 400
    result = await clickhouse_monitor.check()
    by = {c["key"]: c for c in result["checks"]}
    assert result["status"] == "warn" and by["disk"]["status"] == "warn" and by["parts"]["status"] == "warn"
    assert by["disk"]["detail"] == "15 % fri på default"

    ch.free_share = 0.05
    ch.failing_mutations = 2
    ch.applied = []
    result = await clickhouse_monitor.check()
    by = {c["key"]: c for c in result["checks"]}
    assert result["status"] == "error" and by["disk"]["status"] == "error"
    assert by["mutations"]["detail"] == "2 mutation(er) fejler"
    assert "0001_events.sql" in by["migrations"]["detail"]

    ch.down = True
    result = await clickhouse_monitor.check()
    assert result["status"] == "error" and result["checks"][0]["key"] == "reachable" and len(result["checks"]) == 1


async def test_monitor_job_records_and_reports_changes(ch, owner_conn):
    hook = owner_conn.execute("""insert into webhooks (name, url, events, secret_enc)
                                 values ('ops', 'http://127.0.0.1:9/hook', '{clickhouse.status.changed}', '\\x00')
                                 returning id""").fetchone()[0]
    try:
        r1 = await clickhouse_monitor.monitor_job({}, {})
        assert r1["status"] == "ok" and r1["previous"] is None
        # The first run is not a change when it is ok.
        assert owner_conn.execute("select count(*) from audit_log where action = 'clickhouse.status'").fetchone()[0] == 0

        ch.down = True
        r2 = await clickhouse_monitor.monitor_job({}, {})
        assert r2 == {"status": "error", "check_id": r1["check_id"] + 1, "previous": "ok"}
        ch.down = False
        await clickhouse_monitor.monitor_job({}, {})          # back to ok: a second change
        await clickhouse_monitor.monitor_job({}, {})          # still ok: no change

        statuses = [r[0] for r in owner_conn.execute("select status from clickhouse_checks order by id")]
        assert statuses == ["ok", "error", "ok", "ok"]
        audits = owner_conn.execute("""select detail->>'status', detail->>'previous' from audit_log
                                       where action = 'clickhouse.status' order by id""").fetchall()
        assert audits == [("error", "ok"), ("ok", "error")]
        events = owner_conn.execute("""select payload->'data' from webhook_deliveries
                                       where webhook_id = %s order by created_at""", (hook,)).fetchall()
        assert [e[0]["status"] for e in events] == ["error", "ok"] and events[0][0]["check_id"] == r2["check_id"]
    finally:
        owner_conn.execute("delete from webhooks where id = %s", (hook,))


async def test_monitor_job_is_quiet_when_off(monkeypatch, owner_conn):
    owner_conn.execute("truncate clickhouse_checks restart identity")
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, clickhouse_url=None))
    assert await clickhouse_monitor.monitor_job({}, {}) == {"status": "off"}
    assert owner_conn.execute("select count(*) from clickhouse_checks").fetchone()[0] == 0


async def test_overview_shows_the_latest_check(client, ch):
    await full_user(client, "11111111")
    ch.free_share = 0.15
    await clickhouse_monitor.monitor_job({}, {})
    body = (await client.get("/api/admin/clickhouse")).json()
    assert body["latest_check"]["status"] == "warn" and len(body["history"]) == 1
    rows = (await client.get("/api/admin/clickhouse/checks")).json()["rows"]
    assert rows[0]["checks"][1]["key"] == "disk"


# ── Retention ───────────────────────────────────────────────────────────────

async def test_retention_deletes_old_rows_in_clickhouse(ch, owner_conn):
    ch.old_rows = {"events": 5, "system.query_log": 7, "system.part_log": 0}
    owner_conn.execute("update retention_policies set days = 30 where key = 'clickhouse_system_logs'")
    assert await retention.cleanup(only="clickhouse_events") == {"clickhouse_events": 5}
    alter = next(r for r in ch.requests if r.url.params.get("query", "").startswith("ALTER TABLE events DELETE"))
    assert alter.url.params["param_days"] == "365" and alter.url.params["mutations_sync"] == "1"

    assert await retention.cleanup(only="clickhouse_system_logs") == {"clickhouse_system_logs": 7}
    # part_log had nothing old: counted, never mutated.
    assert not any(s.startswith("ALTER TABLE system.part_log") for s in ch.sql())
    row = owner_conn.execute("select last_deleted, last_run_at is not null from retention_policies where key = 'clickhouse_system_logs'").fetchone()
    assert row == (7, True)
    audit = owner_conn.execute("select detail->'deleted' from audit_log where action = 'retention.cleanup' order by id desc limit 1").fetchone()
    assert audit[0] == {"clickhouse_system_logs": 7}


async def test_retention_ignores_a_system_log_that_is_switched_off(ch):
    ch.missing.add("system.part_log")
    ch.old_rows["system.query_log"] = 2
    assert await retention.cleanup(only="clickhouse_system_logs") == {"clickhouse_system_logs": 2}


async def test_retention_failure_is_reported_after_the_rest_ran(ch, owner_conn, make_client):
    ch.old_rows["events"] = 3
    ch.reject_alter = True
    owner_conn.execute("insert into captcha_spent values ('old-ch', now() - interval '3 days')")
    with pytest.raises(retention.CleanupErrors) as e:
        await retention.cleanup()
    assert "clickhouse_events" in e.value.errors and "Memory limit" in e.value.errors["clickhouse_events"]
    # Postgres policies still ran and were committed.
    assert owner_conn.execute("select count(*) from captcha_spent where challenge = 'old-ch'").fetchone()[0] == 0
    audit = owner_conn.execute("select detail->'errors' from audit_log where action = 'retention.cleanup' order by id desc limit 1").fetchone()
    assert "clickhouse_events" in audit[0]

    admin = make_client()
    await full_user(admin, "11111111")
    body = (await admin.post("/api/admin/retention/policies/run")).json()
    assert "clickhouse_events" in body["errors"] and "captcha_spent" in body["deleted"]


async def test_retention_skips_clickhouse_when_off(monkeypatch, owner_conn):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, clickhouse_url=None))
    owner_conn.execute("update retention_policies set last_run_at = null where key like 'clickhouse_%'")
    counts = await retention.cleanup()
    assert "clickhouse_events" not in counts and "clickhouse_system_logs" not in counts
    assert "clickhouse_checks" in counts  # the check history is in Postgres
    rows = owner_conn.execute("""select key from retention_policies
                                 where key in ('clickhouse_events', 'clickhouse_system_logs') and last_run_at is not null""").fetchall()
    assert rows == []
