"""MCP-studie (MCP-9 … MCP-15): the test bench, AI test and saved test cases, monitoring per minute with alarms, and
rate limits per endpoint, user and client.

- **Direct calls** run a tool exactly as over MCP (registry.invoke), as the developer or as one of their test accounts
  (/admin/impersonation), so an external test account sees what an external user sees. Against the draft, an operation
  tool runs the operation's draft version.
- **AI test** sends the endpoint's tool definitions and instructions to the AI layer and runs the tools the model picks,
  as the chosen test account (at most MAX_STEPS calls). Hidden when AI is off.
- **Test cases** are direct calls with expectations or AI questions with the tools (and arguments) the model must
  choose. A major of an endpoint can only be submitted when every test case has passed against the draft (a gate).
- **Monitoring**: job `developer.metrics` turns mcp_log (and the API log) into minute rows (calls, errors, p50/p95,
  cache hits) and tells Responsible when the error rate passes the endpoint's limit or use is unusual.
- **Rate limits**: mcp_endpoints.limits {per_user, per_client, per_endpoint} calls per minute, token buckets in Redis or
  Postgres (app/devkit/cache.py).
"""

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException

from app import ai, config, db, integrations, jobs
from app.devkit import artifacts, cache, operations
from app.logs import RequestMeta
from app.sessions import Principal

MAX_STEPS = 4
STARTER_TOOLS = ("whoami", "access_list", "access_request", "confirm_draft", "cancel_draft", "list_drafts")
DEFAULT_ALARM_PCT = 5.0


# ── Who a test runs as ──────────────────────────────────────────────────────

async def test_principal(admin: Principal, as_user_id: int | None) -> Principal:
    if not as_user_id or as_user_id == admin.user_id:
        return admin
    async with db.connection() as conn:
        u = await db.fetch_one(conn, """select id, email, phone, role, display_name, admin_roles from users where id = %s
                                        and impersonation_only and impersonation_owner_id = %s""", (as_user_id, admin.user_id))
    if u is None:
        raise HTTPException(404, "Testkontoen findes ikke, eller den er ikke din (opret den under /admin/impersonation)")
    meta = RequestMeta(ip=admin.meta.ip, user_agent=admin.meta.user_agent, impersonator_id=admin.user_id)
    return Principal(session_id=admin.session_id, user_id=u["id"], email=u["email"], phone=u["phone"], role=u["role"], scope="full",
                     display_name=u["display_name"], meta=meta, impersonator_id=admin.user_id, admin_roles=tuple(u["admin_roles"] or ()))


async def test_accounts(admin: Principal) -> list[dict]:
    async with db.connection() as conn:
        return await db.fetch_all(conn, """select id, display_name, audience from users where impersonation_only
                                           and impersonation_owner_id = %s order by display_name""", (admin.user_id,))


# ── Calls ───────────────────────────────────────────────────────────────────

async def _draft_operation(conn, endpoint: str, tool: str) -> tuple[dict, dict] | None:
    """For a test against the draft: the operation (and its draft or current version) behind a tool in the endpoint's
    draft version."""
    a = await artifacts.by_slug(conn, "mcp_endpoint", endpoint)
    if a is None:
        return None
    v = await artifacts.open_version(conn, a["id"]) or await artifacts.current(conn, a)
    t = next((x for x in (v["definition"].get("tools") or []) if x["name"] == tool), None) if v else None
    if t is None:
        return None
    op = await artifacts.get(conn, t["operation"])
    ov = await artifacts.open_version(conn, op["id"]) or await artifacts.current(conn, op)
    return op, ov


async def direct_call(endpoint: str, tool: str, args: dict, principal: Principal, *, draft: bool = False) -> dict:
    from app.mcp.registry import Caller, invoke

    started = time.monotonic()
    if draft:
        async with db.connection() as conn:
            found = await _draft_operation(conn, endpoint, tool)
            managed = await artifacts.by_slug(conn, "mcp_endpoint", endpoint)
        if not found and managed is not None and tool not in STARTER_TOOLS:
            return {"result": {"error": f"{tool} findes ikke i kladden"}, "isError": True, "ms": 0, "draft": True}
        if found:
            op, ov = found
            try:
                out = await operations.execute(op, ov, args, principal=principal, user_id=principal.user_id)
            except HTTPException as e:
                return {"result": {"error": e.detail}, "isError": True, "ms": int((time.monotonic() - started) * 1000), "draft": True}
            return {"result": {k: out[k] for k in ("rows", "count", "hasMore", "masked", "version")}, "isError": False,
                    "ms": out["ms"], "cache": out["cache"], "masked": out["masked"], "draft": True}
    result, is_error = await invoke(endpoint, tool, args, Caller(server=endpoint, principal=principal, client_id=None, via="studio"))
    return {"result": result, "isError": is_error, "ms": int((time.monotonic() - started) * 1000),
            "masked": result.get("masked") if isinstance(result, dict) else None,
            "cache": (result.get("cache") or {}).get("status", "miss") if isinstance(result, dict) and "rows" in result else None,
            "draft": False}


async def ai_test(endpoint: str, question: str, principal: Principal) -> dict:
    """MCP-10: which tools an AI client picks, with which arguments, and the answer."""
    from app.mcp.registry import tool_list

    if not ai.enabled():
        raise HTTPException(409, "AI er slået fra på denne instans")
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select instructions from mcp_endpoints where key = %s", (endpoint,))
    tools = [t for t in await tool_list(endpoint) if t["name"] not in ("confirm_draft", "cancel_draft", "access_request", "access_list")]
    fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"][:1000], "parameters": t["inputSchema"]}}
           for t in tools]
    messages = [{"role": "system", "content": (row or {}).get("instructions") or "Brug værktøjerne til at svare. Svar kort på dansk."},
                {"role": "user", "content": question}]
    s = config.settings
    steps, started = [], time.monotonic()
    for _ in range(MAX_STEPS + 1):
        payload = {"model": s.ai_model, "messages": messages, "tools": fns, "temperature": 0, "max_tokens": s.ai_max_output_tokens}
        try:
            async with httpx.AsyncClient(transport=ai.transport, timeout=httpx.Timeout(60, connect=10),
                                         event_hooks=integrations.hooks("ai")) as client:
                r = await client.post(f"{s.ai_base_url.rstrip('/')}/chat/completions", json=payload,
                                      headers={"Authorization": f"Bearer {s.ai_api_key}"})
        except httpx.HTTPError:
            raise HTTPException(424, "AI-tjenesten kunne ikke nås")
        if r.status_code >= 300:
            raise HTTPException(424, f"AI-tjenesten svarede {r.status_code}")
        msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
        calls = msg.get("tool_calls") or []
        if not calls or len(steps) >= MAX_STEPS:
            return {"steps": steps, "answer": (msg.get("content") or "").strip(), "ms": int((time.monotonic() - started) * 1000),
                    "toolsCalled": len(steps), "as": principal.display_name}
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            out = await direct_call(endpoint, fn.get("name") or "", args, principal)
            steps.append({"tool": fn.get("name"), "args": args, "isError": out["isError"], "ms": out["ms"], "cache": out.get("cache"),
                          "masked": out.get("masked")})
            messages.append({"role": "tool", "tool_call_id": c.get("id") or fn.get("name"),
                             "content": json.dumps(out["result"], ensure_ascii=False, default=str)[:12000]})
    return {"steps": steps, "answer": "", "ms": int((time.monotonic() - started) * 1000), "toolsCalled": len(steps)}


# ── Test cases ──────────────────────────────────────────────────────────────

def judge(tc: dict, outcome: dict) -> tuple[bool, list[str]]:
    expect = tc["expect"] or {}
    problems = []
    if tc["kind"] == "direct":
        if outcome["isError"]:
            problems.append(f"Kaldet fejlede: {outcome['result'].get('error')}")
        else:
            count = outcome["result"].get("count")
            if expect.get("min_rows") is not None and (count or 0) < int(expect["min_rows"]):
                problems.append(f"Gav {count} rækker, forventede mindst {expect['min_rows']}")
            if expect.get("contains") and str(expect["contains"]) not in json.dumps(outcome["result"], ensure_ascii=False, default=str):
                problems.append(f"Svaret indeholder ikke “{expect['contains']}”")
    else:
        chosen = [s["tool"] for s in outcome["steps"]]
        for t in expect.get("tools") or []:
            if t not in chosen:
                problems.append(f"AI valgte ikke {t} (valgte {', '.join(chosen) or 'intet'})")
        for t, want in (expect.get("args") or {}).items():
            got = next((s["args"] for s in outcome["steps"] if s["tool"] == t), None)
            for k, v in want.items():
                if got is None or str(got.get(k)) != str(v):
                    problems.append(f"{t}: {k} skulle være {v}")
        if expect.get("answer_contains") and str(expect["answer_contains"]).lower() not in outcome["answer"].lower():
            problems.append(f"Svaret nævner ikke “{expect['answer_contains']}”")
    return not problems, problems


async def run_testcase(tc: dict, admin: Principal, *, draft: bool, version_id: int | None) -> dict:
    principal = await test_principal(admin, tc["as_user_id"])
    if tc["kind"] == "direct":
        outcome = await direct_call(tc["endpoint"], tc["tool"], tc["args"] or {}, principal, draft=draft)
    else:
        outcome = await ai_test(tc["endpoint"], tc["question"], principal)
    passed, problems = judge(tc, outcome)
    detail = {"problems": problems, "ms": outcome.get("ms"),
              "steps": outcome.get("steps"), "answer": (outcome.get("answer") or "")[:2000]}
    async with db.connection() as conn:
        from psycopg.types.json import Jsonb
        await conn.execute("""insert into dev_mcp_test_runs (testcase_id, version_id, passed, detail, ran_by) values (%s, %s, %s, %s, %s)""",
                           (tc["id"], version_id, passed, Jsonb(json.loads(json.dumps(detail, default=str))), admin.user_id))
    return {"id": tc["id"], "name": tc["name"], "passed": passed, **detail}


async def gate(conn, artifact: dict, v: dict) -> str | None:
    """MCP-10: before a major of an endpoint, every saved test case must have passed against this draft. Direct test
    cases have no AI in them and are run here; AI test cases must have been run in the test bench."""
    if v["change_type"] != "major":
        return None
    cases = await db.fetch_all(conn, "select * from dev_mcp_testcases where endpoint = %s", (artifact["slug"],))
    failing = []
    for tc in cases:
        ok = await db.fetch_one(conn, """select passed from dev_mcp_test_runs where testcase_id = %s and version_id = %s
                                         order by id desc limit 1""", (tc["id"], v["id"]))
        if not ok or not ok["passed"]:
            failing.append(tc["name"])
    if failing:
        return ("Gemte testcases skal bestå, før en major udgives. Kør dem mod kladden i testbænken: " + ", ".join(failing))
    return None


artifacts.GATES.setdefault("mcp_endpoint", []).append(gate)


# ── Rate limits ─────────────────────────────────────────────────────────────

async def check_rate(endpoint: str, user_id: int, client_id: str | None) -> tuple[bool, int]:
    if endpoint in ("app", "admin"):
        return True, 0
    async with db.connection() as conn:
        row = await db.fetch_one(conn, "select limits from mcp_endpoints where key = %s", (endpoint,))
    limits = (row or {}).get("limits") or {}
    checks = [(f"mcp:{endpoint}", limits.get("per_endpoint")), (f"mcp:{endpoint}:user:{user_id}", limits.get("per_user"))]
    if client_id:
        checks.append((f"mcp:{endpoint}:client:{client_id}", limits.get("per_client")))
    for key, per_minute in checks:
        if per_minute:
            ok, retry = await cache.take(key, int(per_minute))
            if not ok:
                return False, retry
    return True, 0


# ── Metrics and alarms ──────────────────────────────────────────────────────

AGG_MCP = """
    insert into dev_call_metrics (scope, minute, calls, errors, p50_ms, p95_ms, cache_hits, cache_lookups)
    select 'mcp:' || server, date_trunc('minute', ts), count(*),
           count(*) filter (where status in ('error', 'forbidden', 'auth_failed')),
           percentile_cont(0.5) within group (order by duration_ms)::int, percentile_cont(0.95) within group (order by duration_ms)::int,
           count(*) filter (where cache in ('hit', 'stale')), count(*) filter (where cache is not null)
    from mcp_log where method = 'tools/call' and ts >= %s and ts < date_trunc('minute', now())
    group by 1, 2
    on conflict (scope, minute) do update set calls = excluded.calls, errors = excluded.errors, p50_ms = excluded.p50_ms,
        p95_ms = excluded.p95_ms, cache_hits = excluded.cache_hits, cache_lookups = excluded.cache_lookups"""

AGG_SOURCES: list[str] = [AGG_MCP]     # the API log adds its own aggregate (app/devkit/api_runtime.py)


@jobs.handler("developer.metrics")
async def metrics_job(payload: dict, job: dict) -> dict:
    since = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=int(payload.get("minutes", 10)))
    async with db.connection() as conn:
        for q in AGG_SOURCES:
            await conn.execute(q, (since,))
        await conn.execute("delete from dev_metric_alerts where raised_at < now() - interval '30 days'")
    return {"alerts": await check_alarms()}


async def check_alarms() -> int:
    sent = 0
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, """select scope, sum(calls) as calls, sum(errors) as errors from dev_call_metrics
                                           where minute > now() - interval '15 minutes' group by scope""")
        for r in rows:
            scope, calls, errors = r["scope"], r["calls"], r["errors"]
            kind, key = scope.split(":", 1)
            limits, owner_artifact = {}, None
            if kind == "mcp":
                e = await db.fetch_one(conn, "select limits, artifact_id from mcp_endpoints where key = %s", (key,))
                limits, owner_artifact = (e or {}).get("limits") or {}, (e or {}).get("artifact_id")
            else:
                a = await artifacts.by_slug(conn, "api", key)
                owner_artifact = a["id"] if a else None
                limits = ((await artifacts.current(conn, a)) or {}).get("definition", {}).get("limits", {}) if a else {}
            threshold = float(limits.get("error_alarm_pct") or DEFAULT_ALARM_PCT)
            alarms = []
            if calls >= 20 and errors * 100.0 / calls > threshold:
                alarms.append(("errors", f"Fejlrate {errors * 100.0 / calls:.1f} % de sidste 15 minutter (grænse {threshold:g} %)"))
            avg = await db.fetch_one(conn, """select coalesce(sum(calls), 0) / greatest(1, 7 * 96) as per15 from dev_call_metrics
                                              where scope = %s and minute > now() - interval '7 days' and minute <= now() - interval '15 minutes'""",
                                     (scope,))
            if calls > 100 and avg and calls > 5 * max(1.0, float(avg["per15"])):
                alarms.append(("unusual", f"Usædvanlig brug: {calls} kald de sidste 15 minutter mod normalt {float(avg['per15']):.0f}"))
            for alarm_kind, text in alarms:
                recent = await db.fetch_one(conn, """select 1 from dev_metric_alerts where scope = %s and kind = %s
                                                     and raised_at > now() - interval '1 hour'""", (scope, alarm_kind))
                if recent:
                    continue
                await conn.execute("insert into dev_metric_alerts (scope, kind, detail) values (%s, %s, %s)", (scope, alarm_kind, text))
                if owner_artifact:
                    for uid, rs in (await artifacts.roles(conn, owner_artifact)).items():
                        if "R" in rs:
                            await artifacts.notify(conn, uid, "alarm", f"Alarm: {key}", text,
                                                   link=f"/developer/mcp/drift?endpoint={key}" if kind == "mcp" else f"/developer/api/drift?api={key}",
                                                   artifact_id=owner_artifact)
                sent += 1
    return sent


async def monitor(scope: str) -> dict:
    """The dashboard: today's calls, error rate, p95, cache hit rate, and the last hour per minute."""
    async with db.connection() as conn:
        today = await db.fetch_one(conn, """select coalesce(sum(calls), 0) as calls, coalesce(sum(errors), 0) as errors,
                                            coalesce(sum(cache_hits), 0) as hits, coalesce(sum(cache_lookups), 0) as lookups,
                                            max(p95_ms) as p95_max,
                                            (sum(p95_ms * calls) / nullif(sum(calls), 0))::int as p95,
                                            (sum(p50_ms * calls) / nullif(sum(calls), 0))::int as p50
                                            from dev_call_metrics where scope = %s and minute >= date_trunc('day', now())""", (scope,))
        series = await db.fetch_all(conn, """select minute, calls, errors, p95_ms from dev_call_metrics where scope = %s
                                             and minute > now() - interval '60 minutes' order by minute""", (scope,))
        alerts = await db.fetch_all(conn, "select kind, raised_at, detail from dev_metric_alerts where scope = %s order by raised_at desc limit 10",
                                    (scope,))
    calls = today["calls"]
    return {"callsToday": calls, "errorRate": round(today["errors"] * 100.0 / calls, 1) if calls else 0.0,
            "p50": today["p50"], "p95": today["p95"], "cacheHitRate": round(today["hits"] * 100.0 / today["lookups"]) if today["lookups"] else None,
            "series": series, "alerts": alerts}
