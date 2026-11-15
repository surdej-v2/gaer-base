-- Action-studie (specs/developer/ACTIONS.md): flows — a trigger and a list of steps — run by app/devkit/flow_runs.py.
--
-- A flow is a dev_artifacts row of type 'flow' (versions, RACI, approval like the other studios). The tables here are
-- the runtime: runs with their state (so a run resumes after a crash or a wait), the step log, the approvals people
-- answer, and two lookup tables the triggers use (what a published flow subscribes to, and when a scheduled flow runs
-- next). Runs can hold data from the systems a flow talks to, so they go with the retention policy dev_flow_runs.

alter table dev_artifacts drop constraint dev_artifacts_type_check;
alter table dev_artifacts add constraint dev_artifacts_type_check
    check (type in ('connection', 'dataset', 'pipeline', 'operation', 'multisource', 'mcp_endpoint', 'api', 'api_key', 'flow'));

create table dev_flow_runs (
    id              bigserial primary key,
    artifact_id     bigint not null references dev_artifacts (id) on delete cascade,
    version_id      bigint references dev_artifact_versions (id) on delete set null,
    is_test         boolean not null default false,        -- a test run of the draft from the editor
    trigger         text not null,                         -- manual, schedule, webhook, event, pipeline, flow, rerun
    trigger_data    jsonb not null default '{}',           -- what the trigger brought: body, headers, event, data …
    status          text not null check (status in ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')),
    definition      jsonb not null,                        -- the version as it was when the run started
    state           jsonb not null default '{}',           -- done steps, outputs, variables, loop progress, waits
    output          jsonb,
    error           text,
    waiting         jsonb,                                 -- {kind, until, ref} while status = waiting
    depth           int not null default 0,                -- flows started by flows (or by their own events)
    parent_run_id   bigint references dev_flow_runs (id) on delete set null,
    parent_key      text,
    requested_by    bigint references users (id) on delete set null,
    queued_at       timestamptz not null default now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    steps_done      int not null default 0
);
create index dev_flow_runs_artifact_idx on dev_flow_runs (artifact_id, id desc);
create index dev_flow_runs_open_idx on dev_flow_runs (status) where status in ('queued', 'running', 'waiting');

create table dev_flow_run_steps (
    id              bigserial primary key,
    run_id          bigint not null references dev_flow_runs (id) on delete cascade,
    key             text not null,                         -- the step's place in the definition, e.g. 2/then/0
    step_id         text not null,
    action          text not null,
    attempt         int not null default 1,
    status          text not null check (status in ('running', 'succeeded', 'failed', 'skipped', 'waiting')),
    input           jsonb,                                 -- the rendered `with` (never auth headers)
    output          jsonb,
    error           text,
    started_at      timestamptz not null default now(),
    finished_at     timestamptz,
    unique (run_id, key, attempt)
);

create table dev_flow_approvals (
    id              bigserial primary key,
    run_id          bigint not null references dev_flow_runs (id) on delete cascade,
    step_key        text not null,
    token_hash      text not null unique,                  -- the link in the mail; only its hash is kept
    approvers       bigint[] not null,
    title           text not null,
    message         text not null default '',
    data            jsonb,
    status          text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'expired')),
    decided_by      bigint references users (id) on delete set null,
    comment         text,
    expires_at      timestamptz not null,
    created_at      timestamptz not null default now(),
    decided_at      timestamptz
);

-- What a published, enabled flow is started by: webhook events (app.webhooks.EVENTS), event kinds (app.events),
-- a pipeline's run ("<pipeline-id>:succeeded|failed") or an incoming webhook endpoint (its key).
create table dev_flow_subscriptions (
    artifact_id     bigint not null references dev_artifacts (id) on delete cascade,
    kind            text not null check (kind in ('webhook_event', 'event', 'pipeline', 'webhook')),
    key             text not null,
    primary key (artifact_id, kind, key)
);
create index dev_flow_subscriptions_key_idx on dev_flow_subscriptions (kind, key);

create table dev_flow_schedule (
    artifact_id     bigint primary key references dev_artifacts (id) on delete cascade,
    next_run_at     timestamptz,
    last_run_at     timestamptz
);

insert into retention_policies (key, title, description, days, min_days) values
    ('dev_flow_runs', 'Flow-kørsler (Action-studie)',
     'Kørsler af flows med input og output pr. handling, og de godkendelser de ventede på. Kan indeholde data fra de systemer, et flow taler med.',
     30, 1)
on conflict (key) do nothing;

insert into job_schedules (name, kind, description, interval_seconds) values
    ('flow.schedule', 'flow.schedule', 'Starter flows i Action-studie, når deres tidsplan siger det', 60)
on conflict (name) do nothing;
