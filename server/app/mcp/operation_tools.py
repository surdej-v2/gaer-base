"""Operation tools: an MCP tool made in Datastudie that runs one operation from Gær Developer (app/devkit/operations.py).

The tool's level (classification) has already been checked by toolmap.decide (white, green, yellow with the user's
grant, red with the user's and an approver's). The operation is the published version, masked by its approved policy
before anything is returned (F-10); MCP and the API run the very same function. Arguments are logged with the values
of parameters on non-white fields masked (MCP-14).
"""

from fastapi import HTTPException

from app import db
from app.devkit import artifacts, masking, operations
from app.mcp.registry import Caller, ToolError


def log_args(t: dict, args: dict | None) -> dict | None:
    return masking.mask_args(args, operations.sensitive_params(t.get("op_definition") or {}))


async def run(t: dict, args: dict, caller: Caller) -> dict:
    async with db.connection() as conn:
        a = await db.fetch_one(conn, f"select {artifacts.COLUMNS} from dev_artifacts a where a.id = %s", (t["artifact_id"],))
        if a is None or a["status"] == "disabled":
            raise ToolError("Operationen bag værktøjet er slået fra")
        v = await artifacts.current(conn, a)
    if v is None:
        raise ToolError("Operationen har ingen udgivet version endnu")
    try:
        out = await operations.execute(a, v, args, principal=caller.principal, user_id=caller.user_id)
    except HTTPException as e:
        raise ToolError(str(e.detail))
    result = {"rows": out["rows"], "count": out["count"], "hasMore": out["hasMore"], "masked": out["masked"],
              "version": out["version"], "_cache": out["cache"]}
    if out["cache"] in ("hit", "stale"):
        result["cache"] = {"status": out["cache"], "ageSeconds": out["ageS"]}
    return result
