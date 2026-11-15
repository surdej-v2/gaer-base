"""Data tools: an MCP tool that reads one dataset from the catalog in /admin/data (app/mcp/toolmap.py).

The same reader as Udforsk (routes/data.py): tables in a READ ONLY transaction with a 5 s timeout, API sources
through their kildebeskrivelse. Fields are masked by their classification unless the caller's grant covers them:
yellow fields with any grant, red fields with a confidential grant. Masked fields cannot be filtered on.
"""

from fastapi import HTTPException

from app import db
from app.mcp.registry import Caller, ToolError, ToolOutput
from app.routes import data as data_routes

UNMASKS = {None: set(), "own": set(), "internal": {"yellow"}, "confidential": {"yellow", "red"}}


async def run(t: dict, args: dict, caller: Caller, grant: dict | None) -> ToolOutput:
    async with db.connection() as conn:
        try:
            d = await data_routes._dataset(conn, t["dataset_id"])
        except HTTPException:
            raise ToolError("Datasættet findes ikke længere")
        fields = [f for f in await data_routes._fields(conn, d["id"])
                  if not (d["source_id"] is None and data_routes.SECRET_COLUMN.match(f["name"]))]
    open_classes = UNMASKS[grant["level"] if grant else None]
    masked = {f["name"] for f in fields if f["classification"] in data_routes.MASKED and f["classification"] not in open_classes}
    by_name = {f["name"]: f for f in fields}

    filters = []
    if args.get("field"):
        name = args["field"]
        if name not in by_name:
            raise ToolError(f"Feltet {name} findes ikke. Felter: {', '.join(by_name)}")
        if name in masked:
            raise ToolError(f"{name} er maskeret og kan ikke bruges som filter uden adgang (access_request)")
        filters.append(data_routes.FilterIn(field=name, op="eq", value=args.get("value") or ""))
    body = data_routes.QueryIn(filters=filters, limit=max(1, min(args.get("limit") or 50, 200)),
                               offset=max(0, args.get("offset") or 0))
    try:
        if d.get("kind") in ("foreign", "snapshot"):
            from app.devkit.connections import table_ref
            schema, table = table_ref(d, {"key": d["source_key"]} if d["source_id"] else None)
            result = await data_routes._query_table(table, fields, body, None, False, schema=schema)
        elif d["source_id"] is None:
            result = await data_routes._query_table(d["key"], fields, body, None, False)
        else:
            # The caller stands in for the admin: the source's secrets are resolved by the source's own grant, and the
            # caller's meta (ip, user agent) goes on the audit row when a secret is used.
            result = await data_routes._query_api(d, fields, body, None, False, caller.principal)
    except HTTPException as e:
        raise ToolError(str(e.detail))
    rows = [{k: (data_routes.MASK if k in masked and v is not None else data_routes._plain(v)) for k, v in r.items()}
            for r in result["rows"]]
    columns = [{"name": f["name"], "label": f["label"] or f["name"], "classification": f["classification"],
                "masked": f["name"] in masked} for f in fields]
    text = None
    if masked:
        text = (f"{len(masked)} felter er maskeret. Bed om adgang med access_request(target=\"{t['name']}\") "
                "for at se dem")
    return ToolOutput({"dataset": {"id": d["id"], "name": d["name"], "classification": d["classification"]},
                       "columns": columns, "rows": rows, "total": result.get("total"), "hasMore": result.get("has_more"),
                       "grant": {"level": grant["level"], "expiresAt": grant["expires_at"]} if grant else None},
                      text=text)
