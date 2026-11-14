"""/api/admin/dev/mcp: MCP-studie (MCP-1, MCP-9 … MCP-15) — every endpoint with owners and status, the test bench (direct
calls, AI test, saved test cases), connected clients and their access requests, monitoring and rate limits.
"""

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import db
from app.devkit import artifacts, mcp_studio
from app.devkit.assist import enabled as ai_enabled
from app.logs import audit
from app.mcp import endpoints as endpoints_mod
from app.mcp.registry import tool_list
from app.sessions import Principal, require_admin, revoke_sessions

router = APIRouter(prefix="/api/admin/dev/mcp", tags=["developer"], dependencies=[Depends(require_admin)])


async def _endpoint(conn, key: str) -> dict:
    row = await endpoints_mod.get(conn, key) if endpoints_mod.KEY_RE.match(key) else None
    if row is None:
        raise HTTPException(404, "Endpointet findes ikke")
    return row


@router.get("/endpoints")
async def endpoints():
    """MCP-1: every endpoint with slug, name, audience, version, owners and status."""
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select e.key, e.name, e.audience, e.enabled, e.builtin, e.artifact_id, a.status, a.slug,
                                            v.major, v.minor, v.patch,
                                            (select count(*) from mcp_tool_endpoints t where t.endpoint = e.key) as tools,
                                            (select array_agg(distinct coalesce(u.display_name, split_part(u.email, '@', 1)))
                                               from dev_artifact_roles r join users u on u.id = r.user_id
                                               where r.artifact_id = e.artifact_id and r.role in ('A', 'R')) as owners
                                            from mcp_endpoints e left join dev_artifacts a on a.id = e.artifact_id
                                            left join dev_artifact_versions v on v.id = a.current_version_id
                                            order by e.builtin desc, e.key""")
    out = []
    for r in rows:
        status = "disabled" if not r["enabled"] else (r["status"] or "active")
        out.append({"key": r["key"], "path": endpoints_mod.path_for(r["key"]), "name": r["name"], "audience": r["audience"],
                    "version": f"{r['major']}.{r['minor']}.{r['patch']}" if r["major"] is not None else None,
                    "owners": r["owners"] or [], "status": status, "statusLabel": artifacts.STATUS_LABELS.get(status, status),
                    "origin": "indbygget" if r["builtin"] else "Datastudie" if r["artifact_id"] else "MCP Studio (kode)",
                    "artifactId": r["artifact_id"], "tools": r["tools"]})
    return {"items": out}


@router.get("/{key}/bench")
async def bench(key: str, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        e = await _endpoint(conn, key)
        cases = await db.fetch_all(conn, """select c.*, (select row_to_json(r) from (select passed, ran_at, version_id, detail from dev_mcp_test_runs
                                            where testcase_id = c.id order by id desc limit 1) r) as last
                                            from dev_mcp_testcases c where c.endpoint = %s order by c.id""", (key,))
        draft = None
        if e["artifact_id"]:
            v = await artifacts.open_version(conn, e["artifact_id"])
            draft = artifacts.version_out(v) if v else None
    return {"endpoint": endpoints_mod.out(e), "tools": await tool_list(key), "accounts": await mcp_studio.test_accounts(p),
            "testcases": cases, "ai": ai_enabled(), "draft": draft}


class CallIn(BaseModel):
    tool: str = Field(max_length=48)
    args: dict = Field(default_factory=dict)
    as_user_id: int | None = None
    draft: bool = False


@router.post("/{key}/call")
async def call(key: str, body: CallIn, p: Principal = Depends(require_admin)):
    """MCP-9: a direct call with the answer, masking, time and cache status, as yourself or a test account."""
    async with db.connection() as conn:
        await _endpoint(conn, key)
    principal = await mcp_studio.test_principal(p, body.as_user_id)
    return await mcp_studio.direct_call(key, body.tool, body.args, principal, draft=body.draft)


class AiIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    as_user_id: int | None = None


@router.post("/{key}/ai")
async def ai_test(key: str, body: AiIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _endpoint(conn, key)
    return await mcp_studio.ai_test(key, body.question, await mcp_studio.test_principal(p, body.as_user_id))


class TestcaseIn(BaseModel):
    kind: str = Field(pattern="^(direct|ai)$")
    name: str = Field(min_length=1, max_length=120)
    question: str = Field(default="", max_length=2000)
    tool: str | None = Field(default=None, max_length=48)
    args: dict = Field(default_factory=dict)
    expect: dict = Field(default_factory=dict)
    as_user_id: int | None = None


@router.post("/{key}/testcases", status_code=201)
async def add_testcase(key: str, body: TestcaseIn, p: Principal = Depends(require_admin)):
    if body.kind == "direct" and not body.tool:
        raise HTTPException(422, "Et direkte kald skal have et tool")
    if body.kind == "ai" and not body.question.strip():
        raise HTTPException(422, "En AI-testcase skal have et spørgsmål")
    async with db.connection() as conn:
        e = await _endpoint(conn, key)
        row = await db.fetch_one(conn, """insert into dev_mcp_testcases (endpoint, kind, name, question, tool, args, expect, as_user_id, created_by)
                                          values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                                 (key, body.kind, body.name, body.question, body.tool, Jsonb(body.args), Jsonb(body.expect),
                                  body.as_user_id, p.user_id))
        if e["artifact_id"]:
            await audit(conn, "dev.testcase.created", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=e["artifact_id"],
                        testcase_id=row["id"], kind=body.kind)
    return {"id": row["id"]}


@router.delete("/{key}/testcases/{testcase_id}")
async def delete_testcase(key: str, testcase_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        e = await _endpoint(conn, key)
        n = (await conn.execute("delete from dev_mcp_testcases where id = %s and endpoint = %s", (testcase_id, key))).rowcount
        if not n:
            raise HTTPException(404, "Testcasen findes ikke")
        if e["artifact_id"]:
            await audit(conn, "dev.testcase.deleted", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=e["artifact_id"],
                        testcase_id=testcase_id)
    return {"ok": True}


class RunIn(BaseModel):
    draft: bool = True


@router.post("/{key}/testcases/run")
async def run_testcases(key: str, body: RunIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        e = await _endpoint(conn, key)
        cases = await db.fetch_all(conn, "select * from dev_mcp_testcases where endpoint = %s order by id", (key,))
        version_id = None
        if e["artifact_id"]:
            a = await artifacts.get(conn, e["artifact_id"])
            v = await artifacts.open_version(conn, a["id"]) if body.draft else await artifacts.current(conn, a)
            version_id = v["id"] if v else None
    results = []
    for tc in cases:
        if tc["kind"] == "ai" and not ai_enabled():
            results.append({"id": tc["id"], "name": tc["name"], "passed": False, "problems": ["AI er slået fra"]})
            continue
        results.append(await mcp_studio.run_testcase(tc, p, draft=body.draft, version_id=version_id))
    return {"results": results, "passed": all(r["passed"] for r in results), "versionId": version_id}


@router.get("/{key}/monitor")
async def monitor(key: str):
    async with db.connection() as conn:
        e = await _endpoint(conn, key)
    return {**await mcp_studio.monitor(f"mcp:{key}"), "limits": e["limits"]}


@router.get("/{key}/clients")
async def clients(key: str):
    """MCP-12: who has used the endpoint with which client, and the sessions that can be revoked. A client's token is
    for scope `mcp`, so revoking it ends its access to every endpoint with that scope."""
    async with db.connection() as conn:
        await _endpoint(conn, key)
        rows = await db.fetch_all(conn, """
            select l.client_id, c.name as client_name, l.user_id, coalesce(u.display_name, split_part(u.email, '@', 1)) as user_name,
                   max(l.ts) as last_used, count(*) filter (where l.ts >= date_trunc('day', now())) as calls_today,
                   (select count(*) from sessions s where s.user_id = l.user_id and s.mcp_client_id = l.client_id
                     and s.revoked_at is null and s.expires_at > now()) as sessions
            from mcp_log l left join mcp_clients c on c.id = l.client_id left join users u on u.id = l.user_id
            where l.server = %s and l.client_id is not null group by l.client_id, c.name, l.user_id, u.display_name, u.email
            order by max(l.ts) desc limit 100""", (key,))
        requests = await db.fetch_all(conn, """select r.id, r.status, r.level, r.tool_name, r.client_name, r.created_at,
                                               coalesce(u.display_name, split_part(u.email, '@', 1)) as user_name
                                               from mcp_access_requests r join users u on u.id = r.user_id
                                               where r.endpoint = %s order by r.id desc limit 50""", (key,))
    return {"clients": rows, "requests": requests}


class RevokeIn(BaseModel):
    client_id: str = Field(max_length=200)
    user_id: int


@router.post("/{key}/clients/revoke")
async def revoke(key: str, body: RevokeIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        await _endpoint(conn, key)
        sessions = await db.fetch_all(conn, """select id from sessions where user_id = %s and mcp_client_id = %s and revoked_at is null""",
                                      (body.user_id, body.client_id))
        n = 0
        for s in sessions:
            n += await revoke_sessions(conn, meta=p.meta, user_id=body.user_id, session_id=s["id"], reason="developer.revoke",
                                       actor_id=p.user_id)
        await audit(conn, "session.revoked", actor_id=p.user_id, meta=p.meta, table="sessions", row_id=body.user_id,
                    via="developer", endpoint=key, client=body.client_id, count=n)
    return {"revoked": n}


class LimitsIn(BaseModel):
    per_user: int | None = Field(default=None, ge=0, le=100_000)
    per_client: int | None = Field(default=None, ge=0, le=100_000)
    per_endpoint: int | None = Field(default=None, ge=0, le=1_000_000)
    error_alarm_pct: float | None = Field(default=None, ge=0.1, le=100)


@router.put("/{key}/limits")
async def set_limits(key: str, body: LimitsIn, p: Principal = Depends(require_admin)):
    """MCP-15: rate limits per endpoint, user and client (calls per minute; 0 or empty = none) and the alarm limit."""
    async with db.connection() as conn:
        e = await _endpoint(conn, key)
        limits = {k: v for k, v in body.model_dump().items() if v}
        await conn.execute("update mcp_endpoints set limits = %s, updated_at = now() where key = %s", (Jsonb(limits), key))
        await audit(conn, "mcp_endpoint.limits", actor_id=p.user_id, meta=p.meta, table="mcp_endpoints", row_id=key,
                    before={"limits": e.get("limits")}, after={"limits": limits})
    return {"limits": limits}
