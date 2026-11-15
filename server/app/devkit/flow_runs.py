"""Flow runs (Action-studie): the engine that runs a flow, its triggers and the approvals people answer.

A run (dev_flow_runs) is one execution of one version of a flow. It runs as the job `flow.run`, step by step, and
writes its state after every step, so a container that dies mid-way is no problem: the next job simply resumes where
the state says. A step that must wait — a delay, an approval, a child flow, a pipeline, a retry — does not block a
worker: the run is marked `waiting`, the job ends, and a new job wakes it at the right time (or when the child finishes
or the approver answers). Every executed step is a row in dev_flow_run_steps with its rendered input, output and time.

Rules:
- Actions act with the authority of the flow's Accountable owner (mail, files, Beskeder), audited `via flow:<slug>`.
- HTTP calls go through the SSRF guard (app.webhooks.check_url) or stay inside an API source's hosts; auth comes from
  the API source (Forbind API), never from the definition. Calls are recorded as api.call like every outside API.
- Data from operations is masked by the operation itself. Outputs are kept per step (settings.keep_outputs) and go
  with the retention policy `dev_flow_runs`.
- A flow started by another flow or by its own events carries a depth; more than MAX_DEPTH levels are refused.
- The engine never bypasses anything: a webhook trigger is an ordinary incoming endpoint (verified, rate-limited), an
  event trigger runs in the same transaction as the event, and approvals are decided by logged-in users only.
"""

import asyncio
import contextvars
import html as _html
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app import config, data_sources, db, events, filetree, integrations, jobs, mail, settings_store, source_secrets, webhooks, webhooks_in
from app.devkit import artifacts, assist, expr, flows, operations, pipeline_runs, pipelines
from app.logs import RequestMeta, audit
from app.security import token_hash
from app.sessions import Principal

log = logging.getLogger("gaer.flows")

RUN_DA = {"queued": "I kø", "running": "Kører", "waiting": "Venter", "succeeded": "OK", "failed": "Fejlet", "cancelled": "Afbrudt"}
STEP_DA = {"running": "Kører", "succeeded": "OK", "failed": "Fejlet", "skipped": "Sprunget over", "waiting": "Venter"}
TRIGGER_DA = {"manual": "Manuelt", "schedule": "Tidsplan", "webhook": "Webhook", "event": "Hændelse", "pipeline": "Pipeline",
              "flow": "Andet flow", "rerun": "Kørt igen", "test": "Test"}
MAX_DEPTH = 5
MAX_OUTPUT = 64_000          # characters of JSON kept per step row
MAX_BODY = 1_000_000         # bytes read from an HTTP answer
POLL_S = 30                  # how often a waiting run looks at a child flow or pipeline
RUN_COLUMNS = """r.id, r.artifact_id, r.version_id, r.is_test, r.trigger, r.trigger_data, r.status, r.state, r.definition, r.output,
                 r.error, r.waiting, r.depth, r.parent_run_id, r.parent_key, r.requested_by, r.queued_at, r.started_at,
                 r.finished_at, r.steps_done"""

# Tests replace this with an httpx.MockTransport (the HTTP action).
transport: httpx.AsyncBaseTransport | None = None
# The run that is executing in this task: {"artifact_id", "depth"}. Listeners use it so a flow's own events do not
# start it again, and so depth is counted.
CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("flow_run", default=None)


class ActionError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class _Suspend(Exception):
    """The step cannot finish now: end the job and come back in `seconds` (or when something wakes the run)."""

    def __init__(self, kind: str, seconds: float, ref=None):
        super().__init__(kind)
        self.kind, self.seconds, self.ref = kind, max(0, int(seconds)), ref


class _Terminate(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class FlowFailed(Exception):
    def __init__(self, step_id: str, message: str):
        super().__init__(message)
        self.step_id, self.message = step_id, message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _clip(value) -> tuple[object, bool]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_OUTPUT:
        return _jsonable(value), False
    return {"_afkortet": True, "_tegn": len(text), "uddrag": text[:MAX_OUTPUT]}, True


# ── The artifact type ───────────────────────────────────────────────────────

async def sync_triggers(conn, artifact: dict, definition: dict) -> None:
    """The lookup rows the triggers use (dev_flow_subscriptions) and the schedule, from the published definition."""
    await conn.execute("delete from dev_flow_subscriptions where artifact_id = %s", (artifact["id"],))
    trig = definition.get("trigger") or {}
    rows: list[tuple[str, str]] = []
    if trig.get("type") == "event":
        for name in trig.get("events") or []:
            rows.append(("webhook_event" if name in webhooks.EVENTS else "event", name))
    elif trig.get("type") == "pipeline" and trig.get("pipeline"):
        for status in trig.get("on") or ["succeeded"]:
            rows.append(("pipeline", f"{trig['pipeline']}:{status}"))
    elif trig.get("type") == "webhook" and trig.get("endpoint_key"):
        rows.append(("webhook", trig["endpoint_key"]))
    for kind, key in rows:
        await conn.execute("insert into dev_flow_subscriptions (artifact_id, kind, key) values (%s, %s, %s) on conflict do nothing",
                           (artifact["id"], kind, key))
    if trig.get("type") == "schedule":
        nxt = flows.next_run(trig["cron"], trig.get("tz", "Europe/Copenhagen"))
        await conn.execute("""insert into dev_flow_schedule (artifact_id, next_run_at) values (%s, %s)
                              on conflict (artifact_id) do update set next_run_at = excluded.next_run_at""", (artifact["id"], nxt))
    else:
        await conn.execute("delete from dev_flow_schedule where artifact_id = %s", (artifact["id"],))
    _kinds_cache[0] = 0.0


async def _on_publish(conn, artifact: dict, v: dict, prev: dict | None) -> None:
    await sync_triggers(conn, artifact, v["definition"])


artifacts.register("flow", artifacts.TypeSpec(
    contract=flows.contract, dependencies=flows.dependencies, on_publish=_on_publish,
    other={"description": "Beskrivelse", "trigger": "Udløser", "steps": "Trin", "on_error": "Fejlhåndtering", "settings": "Indstillinger"}))
artifacts.GATES.setdefault("flow", []).append(flows.gate)


# ── Starting a run ──────────────────────────────────────────────────────────

def check_inputs(definition: dict, body: dict) -> dict:
    """The declared inputs of a manual/webhook trigger: required ones must be there (422)."""
    inputs = (definition.get("trigger") or {}).get("inputs") or {}
    body = body if isinstance(body, dict) else {}
    missing = [k for k, spec in inputs.items() if spec.get("required") and body.get(k) in (None, "")]
    if missing:
        raise flows.FlowError("Input mangler: " + ", ".join(missing))
    return body


async def start(conn, artifact: dict, *, trigger: str, data: dict, requested_by: int | None = None,
                version: dict | None = None, is_test: bool = False, parent_run_id: int | None = None,
                parent_key: str | None = None, depth: int = 0) -> int:
    """Queues a run of the published version (or `version`, e.g. the draft for a test). Raises FlowError."""
    if artifact["status"] == "disabled" and not is_test:
        raise flows.FlowError("Flowet er slået fra")
    v = version or await artifacts.current(conn, artifact)
    if v is None:
        raise flows.FlowError("Flowet har ingen udgivet version endnu")
    if depth > MAX_DEPTH:
        raise flows.FlowError(f"Flows kalder hinanden mere end {MAX_DEPTH} led dybt")
    row = await db.fetch_one(conn, """
        insert into dev_flow_runs (artifact_id, version_id, is_test, trigger, trigger_data, status, definition, depth,
                                   parent_run_id, parent_key, requested_by)
        values (%s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s) returning id""",
        (artifact["id"], v["id"], is_test, trigger, Jsonb(_jsonable({"type": trigger, "at": _now().isoformat(), **data})),
         Jsonb(v["definition"]), depth, parent_run_id, parent_key, requested_by))
    await jobs.enqueue(conn, "flow.run", {"run_id": row["id"]}, max_attempts=3, dedupe_key=f"flow.run:{row['id']}")
    return row["id"]


async def _wake(conn, run_id: int, seconds: int = 0) -> None:
    await jobs.enqueue(conn, "flow.run", {"run_id": run_id}, run_in_seconds=seconds, max_attempts=3)


# ── The run ─────────────────────────────────────────────────────────────────

class _Run:
    def __init__(self, run: dict, artifact: dict, owner_id: int | None):
        self.run, self.artifact, self.owner_id = run, artifact, owner_id
        self.definition = run["definition"]
        self.settings = {**flows.SETTINGS_DEFAULT, **(self.definition.get("settings") or {})}
        st = run["state"] or {}
        self.state = {"done": st.get("done") or {}, "steps": st.get("steps") or {}, "vars": st.get("vars") or {},
                      "attempts": st.get("attempts") or {}, "loops": st.get("loops") or {}, "waits": st.get("waits") or {},
                      "refs": st.get("refs") or {}, "error_handled": st.get("error_handled", False)}
        self.ctx: dict = {"trigger": run["trigger_data"], "steps": self.state["steps"], "vars": self.state["vars"],
                          "run": {"id": run["id"], "flow": artifact["slug"], "started_at": (run["started_at"] or _now()).isoformat(),
                                  "test": run["is_test"]}, "error": self.state["vars"].get("_error")}
        started = run["started_at"] or _now()
        self.deadline = started + timedelta(seconds=self.settings["timeout_s"])
        self.steps_done = run["steps_done"]

    async def save(self, conn) -> None:
        """Persists the state and commits: a container that dies loses at most the step it was in."""
        await conn.execute("update dev_flow_runs set state = %s, steps_done = %s where id = %s",
                           (Jsonb(_jsonable(self.state)), self.steps_done, self.run["id"]))
        await conn.commit()

    @property
    def output(self):
        """The run's output: what the last top-level step produced."""
        steps = self.definition.get("steps") or []
        return self.state["steps"].get(steps[-1]["id"]) if steps else None

    async def cancelled(self, conn) -> bool:
        row = await db.fetch_one(conn, "select status from dev_flow_runs where id = %s", (self.run["id"],))
        return row is None or row["status"] == "cancelled"

    def principal(self) -> Principal:
        """The flow acts as its Accountable owner (a developer, hence an admin) — audited via flow:<slug>."""
        return Principal(session_id=0, user_id=self.owner_id or 0, email=None, phone=None, role="admin", scope="full",
                         display_name=f"Flow {self.artifact['slug']}", meta=RequestMeta(), admin_roles=("udvikler",))

    @property
    def via(self) -> str:
        return f"flow:{self.artifact['slug']}"


def _render(rt: _Run, value, what: str):
    try:
        return expr.render(value, rt.ctx)
    except expr.ExprError as e:
        raise ActionError(f"Udtryk i {what}: {e}")


async def _record(conn, rt: _Run, step: dict, key: str, attempt: int, status: str, *, input=None, output=None,
                  error: str | None = None) -> None:
    """The step row: inserted when the step starts (running/waiting), updated when it ends. Times are the database's."""
    inp, _ = _clip(input) if input is not None else (None, False)
    out, _ = _clip(output) if (output is not None and rt.settings["keep_outputs"]) else (None, False)
    await conn.execute("""
        insert into dev_flow_run_steps (run_id, key, step_id, action, attempt, status, input, output, error, finished_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, case when %s in ('running', 'waiting') then null else now() end)
        on conflict (run_id, key, attempt) do update set status = excluded.status, output = excluded.output, error = excluded.error,
            finished_at = excluded.finished_at, input = coalesce(excluded.input, dev_flow_run_steps.input)""",
        (rt.run["id"], key, step["id"], step["action"], attempt, status, Jsonb(inp) if inp is not None else None,
         Jsonb(out) if out is not None else None, (error or None) and error[:2000], status))


async def _run_steps(conn, rt: _Run, steps: list[dict], prefix: str) -> None:
    for i, step in enumerate(steps):
        await _exec_step(conn, rt, step, f"{prefix}{i}")


async def _exec_step(conn, rt: _Run, step: dict, key: str) -> None:
    if key in rt.state["done"]:
        return
    if await rt.cancelled(conn):
        raise _Terminate("cancelled", "Afbrudt")
    if _now() > rt.deadline:
        raise FlowFailed(step["id"], f"Flowet brugte mere end {rt.settings['timeout_s']} sekunder")
    sid, action = step["id"], step["action"]
    if step.get("when"):
        if not expr.truthy(_render(rt, step["when"], f"{sid}.when")):
            await _record(conn, rt, step, key, 1, "skipped")
            rt.state["done"][key] = "skipped"
            await rt.save(conn)
            return
    attempt = rt.state["attempts"].get(key, 0) + 1
    rendered = None
    try:
        if action in ("if", "switch", "foreach"):
            output = await _control(conn, rt, step, key)
        else:
            rendered = _render(rt, step.get("with") or {}, sid)
            if action not in ("set", "compose", "terminate"):
                await _record(conn, rt, step, key, attempt, "running", input=rendered)
            fn = ACTIONS[action]
            # Only the network-bound actions get an outer timeout: cancelling a database write mid-way is worse.
            if action in TIMED:
                output = await asyncio.wait_for(fn(conn, rt, step, rendered, key), timeout=step["timeout_s"] + 5)
            else:
                output = await fn(conn, rt, step, rendered, key)
    except _Suspend:
        await _record(conn, rt, step, key, attempt, "waiting", input=rendered)
        await rt.save(conn)
        raise
    except (_Terminate, FlowFailed):
        raise
    except asyncio.TimeoutError:
        output = await _failed(conn, rt, step, key, attempt, ActionError(f"Handlingen svarede ikke inden {step['timeout_s']} s", retryable=True), rendered)
    except ActionError as e:
        output = await _failed(conn, rt, step, key, attempt, e, rendered)
    except Exception as e:  # noqa: BLE001 — a bug in an action must not take the worker down
        log.exception("flow %s step %s", rt.run["id"], sid)
        output = await _failed(conn, rt, step, key, attempt, ActionError(f"{type(e).__name__}: {str(e)[:200]}"), rendered)
    else:
        await _record(conn, rt, step, key, attempt, "succeeded", input=rendered, output=output)
    rt.state["steps"][sid] = output
    rt.state["done"][key] = True
    rt.steps_done += 1
    await rt.save(conn)


async def _failed(conn, rt: _Run, step: dict, key: str, attempt: int, e: ActionError, rendered):
    """A failed attempt: retry later (a job wakes the run), continue with the error as output, or fail the flow."""
    await _record(conn, rt, step, key, attempt, "failed", input=rendered, error=str(e))
    if e.retryable and attempt <= step["retry"]["count"]:
        rt.state["attempts"][key] = attempt
        await rt.save(conn)
        raise _Suspend("retry", step["retry"]["delay_s"])
    if step["on_error"] == "continue":
        return {"failed": True, "error": str(e)}
    raise FlowFailed(step["id"], str(e))


async def _control(conn, rt: _Run, step: dict, key: str):
    action, sid = step["action"], step["id"]
    if action == "if":
        branch = "then" if expr.truthy(_render(rt, step["when"], f"{sid}.when")) else "else"
        await _run_steps(conn, rt, step.get(branch) or [], f"{key}/{branch}/")
        return {"branch": branch}
    if action == "switch":
        value = expr.to_text(_render(rt, step["value"], f"{sid}.value"))
        case = value if value in step["cases"] else "default"
        await _run_steps(conn, rt, step["cases"].get(value) if case != "default" else step.get("default") or [], f"{key}/{case}/")
        return {"case": case, "value": value}
    loop = rt.state["loops"].setdefault(key, {})
    if "items" not in loop:
        items = _render(rt, step["items"], f"{sid}.items")
        if items is None:
            items = []
        if isinstance(items, dict):
            items = [{"key": k, "value": v} for k, v in items.items()]
        if not isinstance(items, list):
            raise ActionError(f"{sid}: items skal være en liste, ikke {expr.type_name(items)}")
        loop["items"] = items[: step["max_items"]]
        loop["results"] = []
        await rt.save(conn)
    outer = (rt.ctx.get("item"), rt.ctx.get("index"))
    try:
        for idx, item in enumerate(loop["items"]):
            if idx < len(loop["results"]):
                continue
            rt.ctx["item"], rt.ctx["index"] = item, idx
            await _run_steps(conn, rt, step["steps"], f"{key}/{idx}/")
            loop["results"].append({s["id"]: rt.state["steps"].get(s["id"]) for s in step["steps"]})
            await rt.save(conn)
    finally:
        rt.ctx["item"], rt.ctx["index"] = outer
    return {"count": len(loop["items"]), "results": loop["results"]}


# ── Actions ─────────────────────────────────────────────────────────────────

def _list(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return list(value) if isinstance(value, list) else [value]


async def _users(conn, refs: list, what: str) -> list[dict]:
    """Users by id or e-mail (lower case). Unknown ones stop the step."""
    ids = [int(r) for r in refs if isinstance(r, int) or (isinstance(r, str) and r.isdigit())]
    mails = [str(r).strip().lower() for r in refs if isinstance(r, str) and "@" in r]
    rows = await db.fetch_all(conn, """select id, email, phone, display_name from users where blocked_at is null and not impersonation_only
                                       and (id = any(%s) or email = any(%s))""", (ids, mails))
    found = {r["id"] for r in rows} | {r["email"] for r in rows if r["email"]}
    missing = [str(r) for r in ids + mails if r not in found]
    if missing:
        raise ActionError(f"{what}: ukendte eller spærrede brugere: {', '.join(missing[:5])}")
    return rows


async def act_http(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    url = str(w.get("url") or "").strip()
    headers = {str(k): expr.to_text(v) for k, v in (w.get("headers") or {}).items()} if isinstance(w.get("headers"), dict) else {}
    if any(k.lower() in ("authorization", "cookie", "proxy-authorization") for k in headers):
        raise ActionError("Headers må ikke bære nøgler: vælg en API-kilde som auth i stedet")
    source = w.get("source")
    if source:
        src = await db.fetch_one(conn, "select id, key, spec_yaml from data_sources where key = %s and kind = 'rest'", (str(source),))
        if src is None:
            raise ActionError(f"API-kilden {source!r} findes ikke")
        try:
            spec = data_sources.parse_spec(src["spec_yaml"])
            if not url.startswith(("http://", "https://")):
                url = spec["base_url"].rstrip("/") + "/" + url.lstrip("/")
            if urlsplit(url).hostname not in spec["hosts"]:
                raise ActionError("Adressen ligger uden for API-kildens værter")
            values, _ = await source_secrets.resolve(conn, spec, source_id=src["id"])
            headers = {**(await data_sources.build_headers(spec, values)), **headers}
        except data_sources.SpecError as e:
            raise ActionError(f"API-kilden kan ikke læses: {e}")
        except data_sources.FetchError as e:
            raise ActionError(str(getattr(e, "message", e)))
    else:
        try:
            webhooks.check_url(url, resolve=True)
        except webhooks.UnsafeUrl as e:
            raise ActionError(str(e))
    body = w.get("body")
    kwargs: dict = {"params": {str(k): expr.to_text(v) for k, v in (w.get("query") or {}).items()} if isinstance(w.get("query"), dict) else None}
    if body is not None and body != "":
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body).encode()
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
    headers.setdefault("Accept", "application/json, text/*;q=0.8, */*;q=0.5")
    headers.setdefault("User-Agent", "Gaer-flow/1")
    timeout = min(float(w.get("timeout_s") or 30), float(step["timeout_s"]))
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False,
                                     event_hooks=integrations.hooks("flows")) as client:
            r = await client.request(str(w.get("method") or "GET"), url, headers=headers, **kwargs)
            raw = await r.aread()
    except httpx.TimeoutException:
        raise ActionError(f"{url.split('?')[0][:120]} svarede ikke inden {timeout:g} s", retryable=True)
    except httpx.HTTPError as e:
        raise ActionError(f"Kaldet mislykkedes: {type(e).__name__}", retryable=True)
    raw = raw[:MAX_BODY]
    ctype = r.headers.get("content-type", "")
    try:
        parsed = json.loads(raw) if "json" in ctype or raw[:1] in (b"{", b"[") else raw.decode("utf-8", "replace")
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    out = {"status": r.status_code, "ok": r.status_code < 400, "body": parsed,
           "headers": {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "location", "etag", "x-request-id", "retry-after")}}
    if r.status_code >= 400 and w.get("fail_on_error", True):
        raise ActionError(f"{url.split('?')[0][:120]} svarede {r.status_code}", retryable=r.status_code in (408, 425, 429) or r.status_code >= 500)
    return out


async def act_operation(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    args = w.get("args") or {}
    if not isinstance(args, dict):
        raise ActionError("Argumenter skal være et objekt")
    try:
        result = await operations.run_published(w["operation"], args)
    except operations.OperationError as e:
        raise ActionError(f"Operationen: {e.detail}")
    return {k: result.get(k) for k in ("rows", "count", "hasMore", "columns", "masked", "version")}


async def _pipeline_artifact(conn, ref) -> dict:
    a = await flows.find_artifact(conn, "pipeline", ref)
    if a is None:
        raise ActionError(f"Pipelinen {ref!r} findes ikke")
    return a


async def act_pipeline(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    ref = rt.state["refs"].get(key)
    if ref is None:
        a = await _pipeline_artifact(conn, w["pipeline"])
        try:
            run_id = await pipeline_runs.start(conn, a, trigger="flow", requested_by=rt.owner_id)
        except pipelines.PipelineError as e:
            raise ActionError(f"Pipelinen: {e.detail}", retryable="kører allerede" in str(e.detail))
        rt.state["refs"][key] = run_id
        if not w.get("wait", True):
            return {"run_id": run_id, "status": "queued"}
        await rt.save(conn)
        raise _Suspend("pipeline", POLL_S, run_id)
    row = await db.fetch_one(conn, "select status, error, rows from dev_pipeline_runs where id = %s", (ref,))
    if row is None:
        raise ActionError("Pipeline-kørslen forsvandt")
    if row["status"] in ("queued", "running", "waiting"):
        raise _Suspend("pipeline", POLL_S, ref)
    if row["status"] != "succeeded":
        raise ActionError(f"Pipeline-kørslen endte med {pipeline_runs.RUN_DA.get(row['status'], row['status'])}: {row['error'] or ''}".strip())
    return {"run_id": ref, "status": row["status"], "rows": row["rows"]}


async def act_mail(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    refs = _list(w.get("to"))
    direct = [r for r in refs if isinstance(r, str) and "@" in r]
    users = await _users(conn, [r for r in refs if not (isinstance(r, str) and "@" in r)], "Til") if len(direct) != len(refs) else []
    to = sorted({*direct, *(u["email"] for u in users if u["email"])})
    if not to:
        raise ActionError("Ingen modtagere")
    if len(to) > 50:
        raise ActionError("Højst 50 modtagere pr. trin")
    app_name = await settings_store.get(conn, "app_name") or "Gær"
    text = expr.to_text(w.get("text"))
    html_body = expr.to_text(w.get("html")) or "".join(f"<p>{_html.escape(p)}</p>" for p in text.split("\n\n") if p.strip())
    sent = []
    for addr in to:
        try:
            await mail.send_mail(addr, expr.to_text(w.get("subject"))[:200], text, html_body, app_name)
        except mail.MailError as e:
            raise ActionError(f"Mail: {e}")
        except httpx.HTTPError as e:
            raise ActionError(f"Mailudbyderen svarede ikke: {type(e).__name__}", retryable=True)
        sent.append(addr)
    await audit(conn, "flow.mail.sent", actor_id=rt.owner_id, meta=RequestMeta(), table="dev_flow_runs", row_id=rt.run["id"],
                recipients=len(sent), via=rt.via)
    return {"sent": len(sent), "to": sent}


async def act_message(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    from app.messages import service
    users = await _users(conn, _list(w.get("users")), "Brugere")
    if not users:
        raise ActionError("Ingen modtagere")
    channels = [c for c in (_list(w.get("channels")) or ["app", "mail"]) if c in ("app", "mail", "sms")]
    audience = {"kind": "users", "user_ids": [u["id"] for u in users]}
    title, body = expr.to_text(w.get("title"))[:120], expr.to_text(w.get("body"))[:4000]
    est = await service.estimate(conn, audience, channels, title, body)
    row = await service.create_broadcast(conn, title=title, body=body, audience=audience, channels=channels,
                                         urgent=bool(w.get("urgent")), allow_replies=False, send_at=None, est=est,
                                         actor_id=rt.owner_id, confirmation_hash=None)
    await audit(conn, "messages.broadcast.created", actor_id=rt.owner_id, meta=RequestMeta(), table="message_broadcasts",
                row_id=row["id"], recipients=est["total"], channels=channels, via=rt.via)
    return {"broadcast_id": row["id"], "total": est["total"], "sms": est["sms"], "mail": est["mail"]}


async def act_ai(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    if not assist.enabled():
        raise ActionError("AI er ikke slået til på instansen")
    text = await assist.complete(expr.to_text(w.get("system")) or "Du er en hjælpsom assistent. Svar kort og præcist.",
                                 expr.to_text(w.get("prompt")), user_id=rt.owner_id, max_tokens=int(w.get("max_tokens") or 900))
    if text is None:
        raise ActionError("AI svarede ikke", retryable=True)
    out: dict = {"text": text, "json": None}
    if w.get("json"):
        m = re.search(r"\{.*\}|\[.*\]", text, re.S)
        try:
            out["json"] = json.loads(m.group(0)) if m else None
        except ValueError:
            out["json"] = None
        if out["json"] is None:
            raise ActionError("AI svarede ikke med gyldig JSON", retryable=True)
    return out


async def act_event(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    name = expr.to_text(w.get("name"))[:80]
    data = w.get("data") if isinstance(w.get("data"), (dict, list)) else {"value": w.get("data")}
    n = await webhooks.emit(conn, "flow.event", {"flow_id": rt.artifact["id"], "run_id": rt.run["id"], "name": name, "data": _jsonable(data)})
    await events.emit("flow.event", ref=name)
    return {"name": name, "delivered": n}


async def act_flow(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    ref = rt.state["refs"].get(key)
    if ref is None:
        a = await flows.find_artifact(conn, "flow", w["flow"])
        if a is None:
            raise ActionError(f"Flowet {w['flow']!r} findes ikke")
        body = w.get("input") if isinstance(w.get("input"), dict) else {}
        try:
            child = await start(conn, a, trigger="flow", data={"body": check_inputs((await artifacts.current(conn, a) or {}).get("definition") or {}, body),
                                                              "parent_run_id": rt.run["id"]},
                                requested_by=rt.owner_id, parent_run_id=rt.run["id"], parent_key=key, depth=rt.run["depth"] + 1)
        except flows.FlowError as e:
            raise ActionError(str(e))
        rt.state["refs"][key] = child
        if not w.get("wait", True):
            return {"run_id": child, "status": "queued"}
        await rt.save(conn)
        raise _Suspend("flow", POLL_S * 2, child)
    row = await db.fetch_one(conn, "select status, output, error from dev_flow_runs where id = %s", (ref,))
    if row is None:
        raise ActionError("Det kaldte flow forsvandt")
    if row["status"] in ("queued", "running", "waiting"):
        raise _Suspend("flow", POLL_S * 2, ref)
    if row["status"] != "succeeded":
        raise ActionError(f"Det kaldte flow endte med {RUN_DA.get(row['status'], row['status'])}: {row['error'] or ''}".strip())
    return {"run_id": ref, "status": row["status"], "output": row["output"]}


async def act_approval(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    ref = rt.state["refs"].get(key)
    if ref is None:
        people = await _users(conn, _list(w.get("approvers")), "Godkendere")
        if not people:
            raise ActionError("Ingen godkendere")
        hours = max(1, min(24 * 30, int(w.get("timeout_hours") or 72)))
        token = secrets.token_urlsafe(32)
        title = expr.to_text(w.get("title"))[:200]
        row = await db.fetch_one(conn, """
            insert into dev_flow_approvals (run_id, step_key, token_hash, approvers, title, message, data, expires_at)
            values (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(hours => %s)) returning id""",
            (rt.run["id"], key, token_hash(token), [p["id"] for p in people], title, expr.to_text(w.get("message"))[:4000],
             Jsonb(_jsonable(w.get("data"))) if w.get("data") is not None else None, hours))
        rt.state["refs"][key] = row["id"]
        link = f"{config.settings.origin.rstrip('/')}/flow-godkendelse/{token}"
        app_name = await settings_store.get(conn, "app_name") or "Gær"
        for p in people:
            if not p["email"]:
                continue
            try:
                await mail.send_mail(p["email"], f"Godkend: {title}", f"{title}\n\n{expr.to_text(w.get('message'))}\n\nSvar her: {link}",
                                     f"<p>{_html.escape(title)}</p><p>{_html.escape(expr.to_text(w.get('message')))}</p><p><a href=\"{link}\">Svar her</a></p>",
                                     app_name)
            except (mail.MailError, httpx.HTTPError) as e:
                log.warning("approval mail to user %s failed: %s", p["id"], e)
        await audit(conn, "flow.approval.requested", actor_id=rt.owner_id, meta=RequestMeta(), table="dev_flow_runs",
                    row_id=rt.run["id"], approval_id=row["id"], approvers=[p["id"] for p in people], via=rt.via)
        await rt.save(conn)
        raise _Suspend("approval", hours * 3600 + 60, row["id"])
    row = await db.fetch_one(conn, "select status, decided_by, comment, expires_at from dev_flow_approvals where id = %s", (ref,))
    if row is None:
        raise ActionError("Godkendelsen forsvandt")
    if row["status"] == "pending":
        if row["expires_at"] <= _now():
            await conn.execute("update dev_flow_approvals set status = 'expired', decided_at = now() where id = %s", (ref,))
            return {"approved": False, "expired": True, "decided_by": None, "comment": None}
        raise _Suspend("approval", (row["expires_at"] - _now()).total_seconds() + 60, ref)
    return {"approved": row["status"] == "approved", "expired": row["status"] == "expired", "decided_by": row["decided_by"],
            "comment": row["comment"]}


async def act_file(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    content = w.get("content")
    data = (json.dumps(content, ensure_ascii=False, indent=2) if isinstance(content, (dict, list)) else expr.to_text(content)).encode()
    try:
        loc = filetree.parse(expr.to_text(w.get("path")), me=rt.owner_id)
        row = await filetree.write(conn, loc, expr.to_text(w.get("filename")), data, expr.to_text(w.get("content_type")) or None,
                                   rt.principal(), admin=True, overwrite=bool(w.get("overwrite", True)), via=rt.via)
    except HTTPException as e:
        raise ActionError(f"Filen kunne ikke gemmes: {e.detail}")
    return {"file_id": row["id"], "path": loc.path + row["filename"], "size": len(data)}


async def act_set(conn, rt: _Run, step: dict, w: dict, key: str):
    rt.state["vars"][str(w["name"])] = w.get("value")
    return w.get("value")


async def act_compose(conn, rt: _Run, step: dict, w: dict, key: str):
    return w.get("value")


async def act_delay(conn, rt: _Run, step: dict, w: dict, key: str) -> dict:
    until = rt.state["waits"].get(key)
    if until is None:
        if w.get("until"):
            try:
                target = expr.to_datetime(w["until"])
            except expr.ExprError as e:
                raise ActionError(str(e))
        else:
            target = _now() + timedelta(seconds=min(float(w.get("seconds") or 0), 30 * 86400))
        until = rt.state["waits"][key] = target.isoformat()
        await rt.save(conn)
    remaining = (datetime.fromisoformat(until) - _now()).total_seconds()
    if remaining > 0:
        raise _Suspend("delay", remaining, until)
    return {"until": until}


async def act_terminate(conn, rt: _Run, step: dict, w: dict, key: str):
    raise _Terminate("failed" if str(w.get("status") or "succeeded") == "failed" else "succeeded", expr.to_text(w.get("message")))


ACTIONS = {"http": act_http, "operation": act_operation, "pipeline": act_pipeline, "mail": act_mail, "message": act_message,
           "ai": act_ai, "event": act_event, "flow": act_flow, "approval": act_approval, "file": act_file, "set": act_set,
           "compose": act_compose, "delay": act_delay, "terminate": act_terminate}
TIMED = ("http", "ai")


# ── The job ─────────────────────────────────────────────────────────────────

async def _load(conn, run_id: int) -> dict | None:
    return await db.fetch_one(conn, f"select {RUN_COLUMNS} from dev_flow_runs r where r.id = %s", (run_id,))


async def _finish(conn, rt: _Run, status: str, *, error: str | None = None, output=None) -> None:
    out, _ = _clip(output) if output is not None else (None, False)
    await conn.execute("""update dev_flow_runs set status = %s, error = %s, output = %s, waiting = null, finished_at = now(),
                          state = %s, steps_done = %s where id = %s""",
                       (status, error, Jsonb(out) if out is not None else None, Jsonb(_jsonable(rt.state)), rt.steps_done, rt.run["id"]))
    if rt.run["parent_run_id"]:
        await _wake(conn, rt.run["parent_run_id"])
    started = rt.run["started_at"] or _now()
    await events.emit("flow.run", ref=rt.artifact["slug"], value=(_now() - started).total_seconds() * 1000)
    if status == "failed":
        await events.emit("flow.failed", ref=rt.artifact["slug"])
        if not rt.run["is_test"]:
            await webhooks.emit(conn, "flow.run.failed", {"flow_id": rt.artifact["id"], "run_id": rt.run["id"]})
            for uid, rs in (await artifacts.roles(conn, rt.artifact["id"])).items():
                if "R" in rs:
                    await artifacts.notify(conn, uid, "flow_failed", f"{rt.artifact['name']}: kørslen fejlede", (error or "")[:300],
                                           link=f"/developer/actions/koersler/{rt.run['id']}", artifact_id=rt.artifact["id"])


@jobs.handler("flow.run")
async def run_job(payload: dict, job: dict) -> dict | None:
    """One job = the run until it finishes or has to wait. The run is claimed atomically (queued/waiting → running),
    so a second wake-up for the same run simply ends; the state is committed after every step."""
    run_id = int(payload["run_id"])
    async with db.connection() as conn:
        run = await _load(conn, run_id)
        if run is None:
            return {"missing": True}
        if run["status"] in ("succeeded", "failed", "cancelled"):
            return {"skipped": run["status"]}
        artifact = await artifacts.get(conn, run["artifact_id"])
        settings = {**flows.SETTINGS_DEFAULT, **(run["definition"].get("settings") or {})}
        if run["status"] == "queued":
            busy = await db.fetch_one(conn, """select count(*) as n from dev_flow_runs where artifact_id = %s and id <> %s
                                               and status = 'running'""", (artifact["id"], run_id))
            if busy["n"] >= settings["concurrency"]:
                await _wake(conn, run_id, 15)
                return {"deferred": True}
        claimed = await db.fetch_one(conn, """update dev_flow_runs set status = 'running', waiting = null, started_at = coalesce(started_at, now())
                                              where id = %s and status in ('queued', 'waiting') returning id""", (run_id,))
        if claimed is None:
            return {"skipped": "busy"}
        await conn.commit()
        run = await _load(conn, run_id)
        owner = await db.fetch_one(conn, "select user_id from dev_artifact_roles where artifact_id = %s and role = 'A'", (artifact["id"],))
        rt = _Run(run, artifact, owner["user_id"] if owner else run["requested_by"])
        token = CURRENT.set({"artifact_id": artifact["id"], "depth": run["depth"]})
        try:
            try:
                await _run_steps(conn, rt, rt.definition["steps"], "")
                await _finish(conn, rt, "succeeded", output=rt.output)
                return {"status": "succeeded", "steps": rt.steps_done}
            except FlowFailed as e:
                if rt.definition.get("on_error") and not rt.state["error_handled"]:
                    rt.state["error_handled"] = True
                    rt.ctx["error"] = {"step": e.step_id, "message": e.message}
                    rt.state["vars"]["_error"] = rt.ctx["error"]
                    await rt.save(conn)
                    try:
                        await _run_steps(conn, rt, rt.definition["on_error"], "on_error/")
                    except (FlowFailed, _Terminate) as inner:
                        log.warning("flow %s on_error failed: %s", run_id, inner)
                await _finish(conn, rt, "failed", error=f"{e.step_id}: {e.message}"[:2000])
                return {"status": "failed", "step": e.step_id}
            except _Terminate as e:
                if e.status == "cancelled":
                    await conn.execute("update dev_flow_runs set finished_at = coalesce(finished_at, now()) where id = %s", (run_id,))
                    return {"status": "cancelled"}
                await _finish(conn, rt, e.status, error=(e.message or None) if e.status == "failed" else None, output=rt.output)
                return {"status": e.status}
        except _Suspend as s:
            await conn.execute("""update dev_flow_runs set status = 'waiting', waiting = %s where id = %s and status = 'running'""",
                               (Jsonb({"kind": s.kind, "until": (_now() + timedelta(seconds=s.seconds)).isoformat(), "ref": s.ref}), run_id))
            await _wake(conn, run_id, s.seconds)
            return {"status": "waiting", "kind": s.kind, "seconds": s.seconds}
        finally:
            CURRENT.reset(token)


async def _run_dead(conn, job: dict, error: str) -> None:
    await conn.execute("""update dev_flow_runs set status = 'failed', error = %s, finished_at = now()
                          where id = %s and status in ('queued', 'running', 'waiting')""",
                       (f"Jobbet blev opgivet: {error}"[:2000], int(job["payload"]["run_id"])))


jobs.DEAD_HOOKS["flow.run"] = _run_dead


async def cancel(conn, run_id: int, *, actor_id: int, meta: RequestMeta) -> dict:
    row = await db.fetch_one(conn, """update dev_flow_runs set status = 'cancelled', finished_at = now(), waiting = null
                                      where id = %s and status in ('queued', 'running', 'waiting') returning id, artifact_id""", (run_id,))
    if row is None:
        raise HTTPException(409, "Kørslen er allerede færdig")
    await audit(conn, "dev.flow.cancelled", actor_id=actor_id, meta=meta, table="dev_artifacts", row_id=row["artifact_id"], run_id=run_id)
    return row


# ── Triggers ────────────────────────────────────────────────────────────────

async def _start_subscribed(conn, kind: str, key: str, data: dict) -> list[int]:
    """Starts every enabled, published flow subscribed to (kind, key) — not the flow that is emitting right now."""
    cur = CURRENT.get()
    rows = await db.fetch_all(conn, f"""select {artifacts.COLUMNS} from dev_flow_subscriptions s join dev_artifacts a on a.id = s.artifact_id
                                        where s.kind = %s and s.key = %s and a.status <> 'disabled' and a.current_version_id is not null""",
                              (kind, key))
    out = []
    for a in rows:
        if cur and cur["artifact_id"] == a["id"]:
            continue
        try:
            out.append(await start(conn, a, trigger={"webhook": "webhook", "pipeline": "pipeline"}.get(kind, "event"), data=data,
                                   depth=(cur["depth"] + 1) if cur else 0))
        except flows.FlowError as e:
            log.warning("flow %s not started on %s %s: %s", a["slug"], kind, key, e)
    return out


async def on_webhook_event(conn, event: str, data: dict) -> None:
    """Listener on app.webhooks.emit: the same transaction as the change."""
    try:
        async with conn.transaction():
            await _start_subscribed(conn, "webhook_event", event, {"event": event, "data": _jsonable(data)})
            if event in ("snapshot.updated", "pipeline.run.failed") and data.get("pipeline_id"):
                status = "succeeded" if event == "snapshot.updated" else "failed"
                await _start_subscribed(conn, "pipeline", f"{data['pipeline_id']}:{status}",
                                        {"event": event, "pipeline": {"id": data["pipeline_id"], "status": status, "run_id": data.get("run_id")},
                                         "data": _jsonable(data)})
    except Exception:  # noqa: BLE001 — a trigger must never break the change that caused the event
        log.exception("flow trigger on %s", event)


_kinds_cache: list = [0.0, set()]   # [expires (monotonic), kinds]


async def on_events(conn, rows: list[dict]) -> None:
    """Listener on app.events: event kinds flows subscribe to (cached for 30 s)."""
    try:
        if _kinds_cache[0] < time.monotonic():
            subs = await db.fetch_all(conn, "select distinct key from dev_flow_subscriptions where kind = 'event'")
            _kinds_cache[0], _kinds_cache[1] = time.monotonic() + 30, {r["key"] for r in subs}
        hits = [r for r in rows if r["kind"] in _kinds_cache[1]]
        if not hits:
            return
        async with conn.transaction():
            for r in hits:
                await _start_subscribed(conn, "event", r["kind"], {"event": r["kind"], "data": {"ref": r["ref"], "value": r["value"], **(r["data"] or {})}})
    except Exception:  # noqa: BLE001
        log.exception("flow trigger on events")


webhooks.LISTENERS.append(on_webhook_event)
events.LISTENERS.append(on_events)


@webhooks_in.handler("flow", "Starter de flows i Action-studie, der har dette endpoint som udløser")
async def inbound(event: dict, endpoint: dict) -> dict:
    async with db.connection() as conn:
        body = event.get("body_json")
        if body is None and event.get("body"):
            raw = event["body"]
            body = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, memoryview)) else str(raw)
        runs = await _start_subscribed(conn, "webhook", endpoint["key"], {"body": body, "headers": event.get("headers") or {},
                                                                           "event": event.get("event"), "inbound_id": str(event["id"])})
    return {"runs": runs}


@jobs.handler("flow.schedule")
async def schedule_job(payload: dict, job: dict) -> dict:
    started = []
    async with db.connection() as conn:
        due = await db.fetch_all(conn, f"""select s.next_run_at, {artifacts.COLUMNS} from dev_flow_schedule s join dev_artifacts a on a.id = s.artifact_id
                                           where s.next_run_at <= now() and a.status <> 'disabled' and a.current_version_id is not null
                                           for update of s skip locked""")
        for a in due:
            v = await artifacts.current(conn, a)
            trig = (v["definition"].get("trigger") or {}) if v else {}
            if trig.get("type") != "schedule":
                await conn.execute("delete from dev_flow_schedule where artifact_id = %s", (a["id"],))
                continue
            try:
                started.append(await start(conn, a, trigger="schedule", data={"scheduled_for": a["next_run_at"].isoformat()}))
            except flows.FlowError as e:
                log.warning("scheduled flow %s not started: %s", a["slug"], e)
            nxt = flows.next_run(trig["cron"], trig.get("tz", "Europe/Copenhagen"))
            await conn.execute("update dev_flow_schedule set next_run_at = %s, last_run_at = now() where artifact_id = %s", (nxt, a["id"]))
    return {"started": started}


# ── Approvals ───────────────────────────────────────────────────────────────

async def approval_by_token(conn, token: str) -> dict | None:
    return await db.fetch_one(conn, """
        select ap.*, r.artifact_id, a.name as flow_name, a.slug as flow_slug from dev_flow_approvals ap
        join dev_flow_runs r on r.id = ap.run_id join dev_artifacts a on a.id = r.artifact_id where ap.token_hash = %s""",
        (token_hash(token),))


async def decide_approval(conn, token: str, *, user_id: int, approve: bool, comment: str, meta: RequestMeta) -> dict:
    ap = await approval_by_token(conn, token)
    if ap is None:
        raise HTTPException(404, "Godkendelsen findes ikke")
    if user_id not in ap["approvers"]:
        raise HTTPException(403, "Du er ikke blandt godkenderne")
    if ap["status"] != "pending":
        raise HTTPException(409, "Godkendelsen er allerede afgjort")
    if ap["expires_at"] <= _now():
        raise HTTPException(409, "Godkendelsen er udløbet")
    await conn.execute("""update dev_flow_approvals set status = %s, decided_by = %s, comment = %s, decided_at = now() where id = %s""",
                       ("approved" if approve else "rejected", user_id, comment.strip()[:2000], ap["id"]))
    await audit(conn, "flow.approval.decided", actor_id=user_id, meta=meta, table="dev_flow_runs", row_id=ap["run_id"],
                approval_id=ap["id"], approved=approve)
    await _wake(conn, ap["run_id"])
    return await approval_by_token(conn, token)
