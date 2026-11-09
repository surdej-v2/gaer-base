"""/admin/clickhouse: the ClickHouse server in the stack (section "ClickHouse", role Drift).

Read only. The overview shows the server, disks, metrics, the largest tables, the schema and the latest results of
the job clickhouse.monitor (clickhouse_checks); the views are fixed queries (app.clickhouse_monitor.VIEWS).
No SQL is accepted from the browser. Every GET is logged as admin.read by require_admin, as everywhere in /admin.
"""

from fastapi import APIRouter, Depends, HTTPException

from app import clickhouse, clickhouse_monitor, config, db
from app.sessions import require_admin

router = APIRouter(prefix="/api/admin/clickhouse", tags=["admin"], dependencies=[Depends(require_admin)])

HISTORY = 288  # 24 hours of runs every 5 minutes


def _setup() -> dict:
    s = config.settings
    return {
        "configured": clickhouse.configured(),
        "url": clickhouse.redact_url(s.clickhouse_url),
        "database": s.clickhouse_database,
        "user": s.clickhouse_user,
        "views": [{"key": k, "title": t} for k, (t, _) in clickhouse_monitor.VIEWS.items()],
    }


async def _checks(limit: int) -> list[dict]:
    async with db.connection() as conn:
        return await db.fetch_all(conn, """
            select id, checked_at, status, version, checks from clickhouse_checks
            order by id desc limit %s""", (limit,))


@router.get("")
async def overview():
    """Everything the front tab needs in one request (and one audit line)."""
    checks = await _checks(HISTORY)
    out = {**_setup(), "reachable": False, "error": None, "fetched_at": clickhouse_monitor.now(),
           "latest_check": checks[0] if checks else None,
           "history": [{"id": c["id"], "checked_at": c["checked_at"], "status": c["status"]} for c in checks]}
    if not out["configured"]:
        return out
    try:
        out.update(await clickhouse_monitor.overview())
    except clickhouse.ClickHouseError as e:
        out["error"] = e.message
        return out
    out["reachable"] = True
    return out


@router.get("/{view}")
async def view(view: str):
    """One fixed view: tables, queries, mutations, errors, slow, disks — or checks (the monitor's history)."""
    if view == "checks":
        return {"view": view, "title": "Tjek", "fetched_at": clickhouse_monitor.now(), "rows": await _checks(100)}
    if view not in clickhouse_monitor.VIEWS:
        raise HTTPException(404, "Ukendt visning")
    title, sql = clickhouse_monitor.VIEWS[view]
    try:
        rows = await clickhouse.query(sql)
    except clickhouse.ClickHouseError as e:
        raise HTTPException(e.status, e.message)
    return {"view": view, "title": title, "fetched_at": clickhouse_monitor.now(), "rows": rows}
