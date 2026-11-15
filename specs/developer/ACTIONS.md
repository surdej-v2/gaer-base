# Action-studie: flows (a workflow engine in Gær Developer)

`/developer/actions` — the fifth studio. A **flow** is a trigger and a list of **handlinger** (steps), the same idea as
Logic Apps and Power Automate, built on Gær's own parts: flows are artifacts (versions, RACI, approval, contract diff),
runs are jobs (`app.jobs`), triggers are the things Gær already has (incoming webhooks, webhook events, event kinds,
pipeline runs, a cron schedule), and every action acts through an existing module (mail, Beskeder, files, operations,
pipelines, AI) with the authority of the flow's Accountable owner, audited `via flow:<slug>`.

Code: `server/app/devkit/expr.py` (expressions), `flows.py` (definition, validation, cron, the action catalog),
`flow_runs.py` (the engine, triggers, approvals), `server/app/routes/developer_flows.py` (API), migration
`0038_developer_flows.sql`, `web/src/developer/Actions.tsx` (designer, runs), `web/src/pages/FlowApproval.tsx`
(the approver's page). Tests: `server/tests/test_developer_flows.py`.

## The definition

```json
{"trigger": {"type": "schedule", "cron": "0 7 * * 1-5", "tz": "Europe/Copenhagen"},
 "steps": [
   {"id": "hent", "action": "http", "with": {"method": "GET", "url": "/kunder", "source": "crm"}},
   {"id": "hvis", "action": "if", "when": "{{ steps.hent.body.count > 0 }}",
    "then": [{"id": "loop", "action": "foreach", "items": "{{ filter(steps.hent.body.items, 'item.aktiv') }}",
              "steps": [{"id": "mail", "action": "mail", "with": {"to": ["{{ item.email }}"], "subject": "Hej {{ item.navn }}", "text": "…"}}]}],
    "else": []}],
 "on_error": [{"id": "sig_til", "action": "mail", "with": {"to": ["drift@firma.dk"], "subject": "Flow fejlede: {{ error.step }}", "text": "{{ error.message }}"}}],
 "settings": {"timeout_s": 3600, "concurrency": 1, "keep_outputs": true}}
```

| Trigger | What starts the flow | `trigger` holds |
| --- | --- | --- |
| `manual` | »Kør nu« in the studio or `POST /api/admin/dev/flows/{id}/run` with an input; declared `inputs` are checked (required → 422) | `body`, `user_id` |
| `schedule` | cron (5 fields) in a time zone; the job `flow.schedule` runs every minute and starts what is due (`dev_flow_schedule`) | `scheduled_for` |
| `webhook` | an incoming endpoint with handler `flow` (`/admin/webhooks → Indgående`, or made from the studio with a step-up); verified, rate-limited and deduplicated like every incoming webhook | `body`, `headers`, `event` |
| `event` | a webhook event from `app.webhooks.EVENTS` (started in the same transaction as the change) or an event kind from `app.events` (when it is stored) | `event`, `data` |
| `pipeline` | a pipeline run finished (`snapshot.updated` / `pipeline.run.failed`) | `pipeline: {id, status, run_id}` |

Steps: `id` (`^[a-z][a-z0-9_]{0,39}$`, unique), `action`, `name`, `with` (the action's fields), optional `when` (run only
if), `on_error` (`stop` default, or `continue`: the error becomes the step's output `{failed, error}`), `retry`
(`count` 0–10, `delay_s`), `timeout_s` (1–600). Control: `if` (`when`, `then`, `else`), `switch` (`value`, `cases`,
`default`), `foreach` (`items`, `steps`, `max_items`; `item` and `index` in the loop), `delay` (`seconds` or `until`),
`terminate`, `set` (a variable in `vars`), `compose` (a value as output). Actions: `http`, `operation`, `pipeline`,
`mail`, `message` (Beskeder), `ai`, `event`, `flow` (a child flow), `approval`, `file`. The catalog with every field,
help text and output is `flows.ACTIONS` → `GET /api/admin/dev/flows/catalog`; the editor builds its forms from it.

## Expressions (`app/devkit/expr.py`)

`{{ … }}` inside any value. One expression alone keeps its type (a list stays a list); text around it is filled in.
Paths (`trigger.body.kunde.navn`, `steps.hent.body.items[0]`, `vars.x`, `run.id`, `item`, `index`, `error`), literals,
`== != < <= > >= + - * / % and or not in ?? ? :` and a fixed set of functions (text, numbers, lists with a
sub-expression per element — `filter(liste, 'item.aktiv')` — objects, dates as ISO 8601 text, `csv`, `base64`, `uuid`).
A path through null gives null, never an error. **No code is executed**: a closed language, recursion depth and length
are capped, unknown functions and bad syntax are 422 when the definition is saved and a step error at run time.
The editor has »Prøv udtryk« (`POST /api/admin/dev/flows/expr`, optionally against a run's data) and the function list.

## Runs (`app/devkit/flow_runs.py`)

- `start()` writes a `dev_flow_runs` row with the version's definition as it is now and queues the job `flow.run`.
  The job claims the run (`queued`/`waiting` → `running` in one statement, so a second wake-up just ends), walks the
  steps and **commits the state after every step**: a container that dies loses at most the step it was in, and the
  next job resumes from the state (`done`, `steps`, `vars`, loop progress, waits).
- A step that must **wait** — `delay`, `approval`, a child `flow` or `pipeline` with `wait`, a retry with a pause —
  raises `_Suspend`: the run becomes `waiting`, the job ends, and a job wakes it at the right time (and the child
  flow or the approver wakes it at once). Waiting runs hold no worker and do not count toward `concurrency`.
- Every executed step is a `dev_flow_run_steps` row: rendered input (never auth headers), output (unless
  `keep_outputs` is off; clipped at 64 kB), error, attempt, time. The run's output is the last top-level step's output.
- Failure: `retry` first; then `on_error: continue` or the flow fails. `definition.on_error` steps run once with
  `error = {step, message}`; Responsible is notified, `flow.run.failed` is emitted, `events.emit("flow.failed")`.
- `cancel` marks the run; the engine looks at the status at every step boundary. `rerun` starts a new run with the
  same trigger data. A draft is run as a **test** (`is_test`): it never counts as the flow's last run.
- Depth: a flow started by another flow (or by an event it caused) carries `depth`; more than 5 is refused, and a
  flow never starts itself from its own events (`CURRENT` context variable).
- Child flows: `trigger = flow`, `parent_run_id`; when the child finishes it wakes the parent.

## Triggers in detail

- **Schedule** — `flows.next_run(cron, tz)` (own evaluator: `*`, lists, ranges, steps; day-of-month and day-of-week
  OR each other as in cron). On publish, `dev_flow_schedule.next_run_at` is set; `flow.schedule` (every 60 s) starts
  due flows that are enabled and have a published version, then moves `next_run_at`.
- **Webhook event** — `app.webhooks.LISTENERS`: `webhooks.emit()` calls the listener in the same transaction;
  subscribed flows (`dev_flow_subscriptions`, kind `webhook_event`) are started there. A listener error never
  breaks the change (savepoint, logged).
- **Event kind** — `app.events.LISTENERS`: called when events are stored (also from the NATS consumer); the
  subscribed kinds are cached for 30 s.
- **Incoming webhook** — `@webhooks_in.handler("flow")`: the endpoint's key is the subscription (kind `webhook`).
  The studio makes an endpoint with `verify = gaer` (our signature scheme) and shows the secret once; the key goes
  into `trigger.endpoint_key`. A webhook flow cannot be published without it (gate).
- **Pipeline** — subscription `<pipeline-id>:succeeded|failed`, fed by the webhook listener.

## Approvals

`approval` resolves the approvers (user ids or e-mails of active users), writes `dev_flow_approvals` (only the hash of
the link token), mails each approver a link `ORIGIN/flow-godkendelse/<token>` and suspends until the expiry
(`timeout_hours`, default 72). `GET/POST /api/flows/approvals/{token}` need a logged-in user **on the list**
(404 otherwise; test accounts cannot decide); one decision, audited `flow.approval.decided`, then the run is woken.
The step's output is `{approved, decided_by, comment, expired}` — a rejection or an expiry does not fail the flow;
the flow branches on `steps.<id>.approved`.

## Security and rules

- **HTTP**: without an API source the address goes through `webhooks.check_url(resolve=True)` (https, public
  address); with `source` the auth comes from the API source (Forbind API, secrets from Hemmeligheder) and the
  address must stay inside the source's hosts. `Authorization`/`Cookie` headers in a definition are refused.
  Calls are recorded as `api.call` (integration `flows`). Redirects are not followed; the body is capped at 1 MB.
- **Who may**: everything under `/api/admin/dev/flows` is `require_admin` + the admin-role table (section
  `/developer`, roles Udvikler, Udvikler-admin, Data, Fuld). Changing a draft, testing it or making its endpoint
  needs an owner (A or R) or a Udvikler-admin. Publishing goes through the shared submit/approval of the core.
- **Audit**: artifact changes by the core (`dev.version.saved`, …); `dev.flow.run` (manual, test, rerun),
  `dev.flow.cancelled`, `flow.mail.sent`, `messages.broadcast.created` (via flow), `flow.approval.requested`,
  `flow.approval.decided`, files via `filetree.write(via="flow:<slug>")`.
- **Personal data**: run rows can hold data from the systems a flow talks to (webhook bodies, HTTP answers), so
  they follow the retention policy `dev_flow_runs` (30 days, `retention.CLEANUP_SQL`) and `keep_outputs` can be
  switched off per flow. Operation data is masked by the operation. `flow.event` carries what the flow puts in
  `data`: ids, never personal data. Approvals: an approver's export lists them (`flow_approvals`); an erased user
  leaves the approver lists (`erase_user`).
- **Limits**: 200 steps, 50 inputs, `max_items` ≤ 10 000, `concurrency` 1–20, `timeout_s` 10–86 400, depth 5,
  50 mail recipients per step, delay ≤ 30 days.

## What is not here yet

Approvals inside the app's inbox (Beskeder) instead of mail only; a `respond` action for synchronous webhook answers;
`until`/`while` loops; connectors with delegated (per-user) OAuth — those arrive with the login-provider work;
saving a run as a test case.
