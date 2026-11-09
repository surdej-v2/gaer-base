"""Applies the ClickHouse schema in app/clickhouse_migrations/ in order. Called by python -m app.migrate (inside its
Postgres advisory lock, so two deploys do not migrate at once) when CLICKHOUSE_URL is set.

ClickHouse has no transactions: each statement runs on its own and the file is recorded in schema_migrations (in the
ClickHouse database) after its last statement. Statements must therefore be safe to run twice (IF NOT EXISTS).
Like the Postgres migrations, an applied file is never edited; a change is a new file.
"""

import pathlib
import re

from app import clickhouse, config

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "clickhouse_migrations"
_END = re.compile(r";[ \t]*(?:\n|$)")

SCHEMA_TABLE = """CREATE TABLE IF NOT EXISTS schema_migrations (
    name        String,
    applied_at  DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY name"""


def migration_files() -> list[pathlib.Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def statements(text: str) -> list[str]:
    """Splits a file on ";" at the end of a line; drops chunks that are only comments."""
    out = []
    for chunk in _END.split(text):
        code = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
        if code:
            out.append(code)
    return out


def applied() -> set[str]:
    return {r["name"] for r in clickhouse.query_sync("SELECT name FROM schema_migrations")}


async def pending() -> list[str]:
    """Files not applied yet (for the monitor and /admin/clickhouse). A missing schema_migrations means all."""
    try:
        done = {r["name"] for r in await clickhouse.query("SELECT name FROM schema_migrations")}
    except clickhouse.ClickHouseError as e:
        if e.code not in (60, 81):  # UNKNOWN_TABLE, UNKNOWN_DATABASE
            raise
        done = set()
    return [p.name for p in migration_files() if p.name not in done]


def migrate(verbose: bool = True) -> list[str]:
    """Applies pending files. Returns their names; does nothing without CLICKHOUSE_URL."""
    if not clickhouse.configured():
        return []
    database = clickhouse.identifier(config.settings.clickhouse_database)
    clickhouse.execute_sync(f"CREATE DATABASE IF NOT EXISTS {database}", database="system")
    clickhouse.execute_sync(SCHEMA_TABLE)
    done = applied()
    applied_now = []
    for path in migration_files():
        if path.name in done:
            continue
        if verbose:
            print(f"migrating clickhouse {path.name}")
        for statement in statements(path.read_text()):
            clickhouse.execute_sync(statement)
        clickhouse.execute_sync("INSERT INTO schema_migrations (name) SELECT {name:String}", {"name": path.name})
        applied_now.append(path.name)
    if verbose:
        print(f"{len(applied_now)} clickhouse migration(s) applied" if applied_now else "clickhouse is up to date")
    return applied_now
