"""Action-studie (/developer/actions): flows as artifacts, their runs, and the approvals people answer.

  GET    /api/admin/dev/flows                      the list with trigger, version, last and next run
  POST   /api/admin/dev/flows                      a new flow as a draft (slug, name, trigger type)
  GET    /api/admin/dev/flows/catalog              actions, triggers, functions, events and the things a step can point at
  POST   /api/admin/dev/flows/validate             the definition cleaned, or 422 with what is wrong
  POST   /api/admin/dev/flows/expr                 try an expression against a sample context (the editor)
  GET    /api/admin/dev/flows/runs[?flow=]          runs across flows · GET …/runs/{id} one run with its steps
  POST   /api/admin/dev/flows/runs/{id}/cancel|rerun
  GET    /api/admin/dev/flows/{id}                 the artifact, the definition (draft or published), runs, next run
  PUT    /api/admin/dev/flows/{id}                 save the draft (validated; the change type is decided by the core)
  POST   /api/admin/dev/flows/{id}/run             Kør nu with an input; {"draft": true} runs the draft as a test
  POST   /api/admin/dev/flows/{id}/webhook-endpoint  an incoming endpoint with handler `flow` for the webhook trigger (step-up)
  GET    /api/flows/approvals/{token}              what a logged-in approver sees · POST …: approve or reject

Everything under /api/admin/dev is guarded by require_admin and the admin-role table (section /developer); submit,
RACI and approval of versions are the shared artifact endpoints in routes/developer.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import admin_roles, db, secrets_box, webhooks, webhooks_in
from app.devkit import artifacts, expr, flow_runs, flows
from app.logs import audit
from app.sessions import Principal, current_user, require_admin, require_admin_stepup

router = APIRouter(prefix="/api/admin/dev/flows", tags=["developer"], dependencies=[Depends(require_admin)])
public = APIRouter(prefix="/api/flows", tags=["flows"])

FLOW_RUN_COLUMNS = """r.id, r.artifact_id, r.is_test, r.trigger, r.status, r.error, r.waiting, r.depth, r.parent_run_id,
                      r.requested_by, r.queued_at, r.started_at, r.finished_at, r.steps_done"""


def _run_out(r: dict, name: str | None = None, slug: str | None = None) -> dict:
    return {**r, "label": flow_runs.RUN_DA.get(r["status"], r["status"]), "triggerLabel": flow_runs.TRIGGER_DA.get(r["trigger"], r["trigger"]),
            **({"name": name, "slug": slug} if name is not None else {})}


async def _flow(conn, flow_id: int) -> dict:
    a = await artifacts.get(conn, flow_id)
    if a["type"] != "flow":
        raise HTTPException(404, "Flowet findes ikke")
    return a


async def _can_edit(conn, a: dict, p: Principal) -> None:
    if admin_roles.is_dev_admin(p.admin_roles) or await artifacts.is_owner(conn, a["id"], p.user_id, ("A", "R")):
        return
    raise HTTPException(403, "Kun flowets ejere (A eller R) eller en Udvikler-admin kan ændre det")


# ── The list and the catalog ────────────────────────────────────────────────

@router.get("")
async def list_flows():
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""
            select {artifacts.COLUMNS}, v.major, v.minor, v.patch, v.definition,
                   (select status from dev_artifact_versions o where o.artifact_id = a.id and o.status in ('draft', 'pending')) as open_status,
                   s.next_run_at
            from dev_artifacts a left join dev_artifact_versions v on v.id = a.current_version_id
            left join dev_flow_schedule s on s.artifact_id = a.id
            where a.type = 'flow' order by a.name""")
        last = {r["artifact_id"]: r for r in await db.fetch_all(conn, f"""
            select distinct on (r.artifact_id) {FLOW_RUN_COLUMNS} from dev_flow_runs r where not r.is_test
            order by r.artifact_id, r.id desc""")}
        counts = {r["artifact_id"]: r for r in await db.fetch_all(conn, """
            select artifact_id, count(*) filter (where status = 'failed' and finished_at > now() - interval '7 days') as failed_7d,
                   count(*) filter (where status in ('queued', 'running', 'waiting')) as open
            from dev_flow_runs where not is_test group by artifact_id""")}
    items = []
    for r in rows:
        trig = (r["definition"] or {}).get("trigger") or {} if r["definition"] else {}
        items.append({**artifacts.out(r, {"major": r["major"], "minor": r["minor"], "patch": r["patch"]} if r["major"] is not None else None),
                      "trigger": trig.get("type"), "triggerText": flows.trigger_text(trig) if trig else "—",
                      "steps": flows.count_steps((r["definition"] or {}).get("steps") or []) if r["definition"] else 0,
                      "draft": r["open_status"], "next": r["next_run_at"],
                      "lastRun": _run_out(last[r["id"]]) if r["id"] in last else None,
                      "failed7d": counts.get(r["id"], {}).get("failed_7d", 0), "open": counts.get(r["id"], {}).get("open", 0)})
    return {"items": items}


@router.get("/catalog")
async def catalog():
    async with db.connection() as conn:
        ops = await db.fetch_all(conn, """select a.id, a.slug, a.name, a.type from dev_artifacts a
                                          where a.type in ('operation', 'pipeline', 'flow') and a.status <> 'disabled' order by a.type, a.name""")
        sources = await db.fetch_all(conn, "select key, name from data_sources where kind = 'rest' order by name")
        endpoints = await db.fetch_all(conn, "select key, name, active from webhook_endpoints where handler = 'flow' order by name")
    out = flows.catalog()
    out["operations"] = [o for o in ops if o["type"] == "operation"]
    out["pipelines"] = [o for o in ops if o["type"] == "pipeline"]
    out["flows"] = [o for o in ops if o["type"] == "flow"]
    out["sources"] = sources
    out["endpoints"] = endpoints
    return out


class CreateIn(BaseModel):
    slug: str = Field(pattern=artifacts.SLUG_RE, max_length=63)
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)
    trigger: str = Field(default="manual", max_length=20)


@router.post("", status_code=201)
async def create_flow(body: CreateIn, p: Principal = Depends(require_admin)):
    if body.trigger not in flows.TRIGGERS:
        raise HTTPException(422, "Ukendt udløser")
    trigger: dict = {"type": body.trigger, "inputs": {}}
    if body.trigger == "schedule":
        trigger |= {"cron": "0 7 * * 1-5", "tz": "Europe/Copenhagen"}
    elif body.trigger == "event":
        trigger["events"] = ["user.created"]
    definition = {"trigger": trigger, "steps": [], "on_error": [], "settings": dict(flows.SETTINGS_DEFAULT), "description": body.description}
    async with db.connection() as conn:
        if body.trigger == "pipeline":
            first = await db.fetch_one(conn, "select id from dev_artifacts where type = 'pipeline' order by id limit 1")
            if first is None:
                raise HTTPException(422, "Der er ingen pipelines at udløse på endnu")
            definition["trigger"] |= {"pipeline": first["id"], "on": ["succeeded"]}
        a = await artifacts.create(conn, type_="flow", slug=body.slug, name=body.name.strip() or body.slug, definition=definition,
                                   actor_id=p.user_id, meta=p.meta, description=body.description, publish=False)
    return {"id": a["id"], "slug": a["slug"]}


class DefinitionIn(BaseModel):
    definition: dict


@router.post("/validate")
async def validate(body: DefinitionIn):
    async with db.connection() as conn:
        clean = await flows.resolve(conn, flows.validate(body.definition))
    return {"definition": clean, "steps": flows.count_steps(clean["steps"]), "trigger": flows.trigger_text(clean["trigger"])}


class ExprIn(BaseModel):
    expression: str = Field(max_length=4000)
    context: dict = Field(default_factory=dict)
    run_id: int | None = None


@router.post("/expr")
async def try_expression(body: ExprIn):
    """The editor's »Prøv udtryk«: against a context you give, or the data of a run (its trigger, step outputs, vars)."""
    ctx = dict(body.context)
    if body.run_id:
        async with db.connection() as conn:
            run = await db.fetch_one(conn, "select trigger_data, state from dev_flow_runs where id = %s", (body.run_id,))
        if run:
            st = run["state"] or {}
            ctx = {"trigger": run["trigger_data"], "steps": st.get("steps") or {}, "vars": st.get("vars") or {}, **ctx}
    try:
        value = expr.render(body.expression, ctx)
    except expr.ExprError as e:
        return {"ok": False, "error": e.message}   # our own Danish message, never a stack trace
    clipped, truncated = flow_runs._clip(value)
    return {"ok": True, "value": clipped, "type": expr.type_name(value), "truncated": truncated}


# ── Runs ────────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(flow: int | None = None, status: str | None = Query(default=None, max_length=20),
                    limit: int = Query(default=100, ge=1, le=500)):
    where, params = ["true"], []
    if flow:
        where.append("r.artifact_id = %s")
        params.append(flow)
    if status:
        where.append("r.status = %s")
        params.append(status)
    async with db.connection() as conn:
        rows = await db.fetch_all(conn, f"""select {FLOW_RUN_COLUMNS}, a.name, a.slug from dev_flow_runs r join dev_artifacts a on a.id = r.artifact_id
                                            where {' and '.join(where)} order by r.id desc limit %s""", (*params, limit))
    return {"items": [_run_out(r, r["name"], r["slug"]) for r in rows]}


@router.get("/runs/{run_id}")
async def run_detail(run_id: int):
    async with db.connection() as conn:
        r = await db.fetch_one(conn, f"""select {FLOW_RUN_COLUMNS}, r.trigger_data, r.output, r.state, r.definition, a.name, a.slug
                                         from dev_flow_runs r join dev_artifacts a on a.id = r.artifact_id where r.id = %s""", (run_id,))
        if r is None:
            raise HTTPException(404, "Kørslen findes ikke")
        steps = await db.fetch_all(conn, """select id, key, step_id, action, attempt, status, input, output, error, started_at, finished_at
                                            from dev_flow_run_steps where run_id = %s order by started_at, id""", (run_id,))
        approvals = await db.fetch_all(conn, """select id, step_key, title, status, approvers, decided_by, comment, expires_at, created_at, decided_at
                                                from dev_flow_approvals where run_id = %s order by id""", (run_id,))
        children = await db.fetch_all(conn, f"""select {FLOW_RUN_COLUMNS}, a.name, a.slug from dev_flow_runs r join dev_artifacts a on a.id = r.artifact_id
                                                where r.parent_run_id = %s order by r.id""", (run_id,))
    state = r.pop("state") or {}
    return {**_run_out(r, r["name"], r["slug"]), "vars": state.get("vars") or {},
            "steps": [{**s, "label": flow_runs.STEP_DA.get(s["status"], s["status"]), "actionLabel": flows.ACTIONS.get(s["action"], {}).get("label", s["action"])}
                      for s in steps],
            "approvals": approvals, "children": [_run_out(c, c["name"], c["slug"]) for c in children]}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        row = await flow_runs.cancel(conn, run_id, actor_id=p.user_id, meta=p.meta)
    return {"id": row["id"], "status": "cancelled"}


@router.post("/runs/{run_id}/rerun")
async def rerun(run_id: int, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        old = await db.fetch_one(conn, "select artifact_id, trigger_data, is_test from dev_flow_runs where id = %s", (run_id,))
        if old is None:
            raise HTTPException(404, "Kørslen findes ikke")
        a = await _flow(conn, old["artifact_id"])
        data = {k: v for k, v in (old["trigger_data"] or {}).items() if k not in ("type", "at")}
        try:
            new_id = await flow_runs.start(conn, a, trigger="rerun", data={**data, "rerun_of": run_id}, requested_by=p.user_id)
        except flows.FlowError as e:
            raise HTTPException(409, str(e))
        await audit(conn, "dev.flow.run", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=a["id"], run_id=new_id, rerun_of=run_id)
    return {"run_id": new_id}


# ── One flow ────────────────────────────────────────────────────────────────

@router.get("/{flow_id}")
async def detail(flow_id: int):
    async with db.connection() as conn:
        a = await _flow(conn, flow_id)
        art = await artifacts.detail(conn, flow_id)
        runs = await db.fetch_all(conn, f"select {FLOW_RUN_COLUMNS} from dev_flow_runs r where r.artifact_id = %s order by r.id desc limit 30", (flow_id,))
        sched = await db.fetch_one(conn, "select next_run_at, last_run_at from dev_flow_schedule where artifact_id = %s", (flow_id,))
        subs = await db.fetch_all(conn, "select kind, key from dev_flow_subscriptions where artifact_id = %s order by kind, key", (flow_id,))
        definition = (art["draft"] or art["current"] or {}).get("definition") or {}
        endpoint = None
        key = (definition.get("trigger") or {}).get("endpoint_key")
        if key:
            endpoint = await db.fetch_one(conn, "select key, name, verify, active, header_name from webhook_endpoints where key = %s", (key,))
    out = {"artifact": art, "definition": definition, "runs": [_run_out(r) for r in runs],
           "next": sched["next_run_at"] if sched else None, "lastScheduled": sched["last_run_at"] if sched else None,
           "subscriptions": subs, "triggerText": flows.trigger_text(definition.get("trigger") or {}),
           "endpoint": {**endpoint, "url": f"{webhooks_in_origin()}/api/hooks/in/{endpoint['key']}"} if endpoint else None}
    return out


def webhooks_in_origin() -> str:
    from app import config
    return config.settings.origin.rstrip("/")


@router.put("/{flow_id}")
async def save(flow_id: int, body: DefinitionIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await _flow(conn, flow_id)
        await _can_edit(conn, a, p)
        clean = await flows.resolve(conn, flows.validate(body.definition))
        v = await artifacts.save_draft(conn, a, clean, actor_id=p.user_id, meta=p.meta)
    return {"version": artifacts.version_out(v), "definition": clean}


class RunIn(BaseModel):
    input: dict = Field(default_factory=dict)
    draft: bool = False


@router.post("/{flow_id}/run")
async def run_now(flow_id: int, body: RunIn, p: Principal = Depends(require_admin)):
    async with db.connection() as conn:
        a = await _flow(conn, flow_id)
        version = None
        if body.draft:
            await _can_edit(conn, a, p)
            version = await artifacts.open_version(conn, flow_id)
            if version is None:
                raise HTTPException(409, "Der er ingen kladde at teste")
        v = version or await artifacts.current(conn, a)
        if v is None:
            raise HTTPException(409, "Flowet har ingen udgivet version endnu")
        try:
            flow_runs.check_inputs(v["definition"], body.input)
            run_id = await flow_runs.start(conn, a, trigger="test" if body.draft else "manual", data={"body": body.input, "user_id": p.user_id},
                                           requested_by=p.user_id, version=version, is_test=body.draft)
        except flows.FlowError as e:
            raise HTTPException(409 if "ingen udgivet" in str(e) or "slået fra" in str(e) else 422, str(e))
        await audit(conn, "dev.flow.run", actor_id=p.user_id, meta=p.meta, table="dev_artifacts", row_id=flow_id, run_id=run_id, test=body.draft)
    return {"run_id": run_id}


@router.post("/{flow_id}/webhook-endpoint")
async def make_endpoint(flow_id: int, p: Principal = Depends(require_admin_stepup)):
    """An incoming endpoint (verified with our own signature scheme) whose handler starts this flow. The secret is
    shown once; the key goes into the trigger (endpoint_key) when the draft is saved."""
    secret = webhooks.new_secret()
    async with db.connection() as conn:
        a = await _flow(conn, flow_id)
        await _can_edit(conn, a, p)
        row = await db.fetch_one(conn, """
            insert into webhook_endpoints (name, key, verify, header_name, secret_enc, secret_hint, handler, active, created_by)
            values (%s, %s, 'gaer', 'X-Gaer-Signature', %s, %s, 'flow', true, %s) returning id, key""",
            (f"Flow: {a['name']}"[:80], webhooks_in.new_key(), secrets_box.encrypt(secret, "webhook-in"), "…" + secret[-4:], p.user_id))
        await audit(conn, "create", actor_id=p.user_id, meta=p.meta, table="webhook_endpoints", row_id=row["id"],
                    after={"name": f"Flow: {a['name']}", "verify": "gaer", "handler": "flow", "flow_id": flow_id})
    return {"key": row["key"], "url": f"{webhooks_in_origin()}/api/hooks/in/{row['key']}", "secret": secret, "verify": "gaer"}


# ── Approvals (any logged-in user who is on the list) ──────────────────────

def _approval_out(ap: dict, user_id: int) -> dict:
    return {"id": ap["id"], "title": ap["title"], "message": ap["message"], "data": ap["data"], "status": ap["status"],
            "flow": ap["flow_name"], "expiresAt": ap["expires_at"], "decidedAt": ap["decided_at"], "comment": ap["comment"],
            "mine": user_id in ap["approvers"], "decidedByMe": ap["decided_by"] == user_id}


@public.get("/approvals/{token}")
async def approval(token: str, p: Principal = Depends(current_user)):
    async with db.connection() as conn:
        ap = await flow_runs.approval_by_token(conn, token)
    if ap is None or p.user_id not in ap["approvers"] and ap["decided_by"] != p.user_id:
        raise HTTPException(404, "Godkendelsen findes ikke, eller den er ikke til dig")
    return _approval_out(ap, p.user_id)


class DecisionIn(BaseModel):
    approve: bool
    comment: str = Field(default="", max_length=2000)


@public.post("/approvals/{token}")
async def decide(token: str, body: DecisionIn, p: Principal = Depends(current_user)):
    if p.impersonator_id is not None:
        raise HTTPException(403, "Ikke muligt for en testkonto")
    async with db.connection() as conn:
        ap = await flow_runs.decide_approval(conn, token, user_id=p.user_id, approve=body.approve, comment=body.comment, meta=p.meta)
    return _approval_out(ap, p.user_id)
