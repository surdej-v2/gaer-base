-- Gær Developer, fase 4 (specs/developer/MCP.md): MCP-studiets testbænk, testcases, overvågning og rate limits.
--
-- mcp_endpoints.limits   rate limits pr. endpoint, bruger og klient (kald pr. minut) og alarmgrænsen for fejlrate.
-- dev_mcp_testcases      gemte testcases pr. endpoint: et direkte kald med forventninger, eller et spørgsmål til
--                        AI-testen med de tools og argumenter, AI'en skal vælge (MCP-10).
-- dev_mcp_test_runs      resultatet pr. kørsel og version. En major udgives først, når alle testcases har bestået
--                        mod kladden.
-- dev_call_metrics       kald, fejl, p50/p95 og cache-træf pr. minut for MCP-endpoints og API'er (MCP-14, API-11).
-- dev_metric_alerts      sendte alarmer (højst én pr. time pr. slags), så Responsible ikke drukner.

alter table mcp_endpoints add column limits jsonb not null default '{}';

create table dev_mcp_testcases (
    id          bigserial primary key,
    endpoint    text not null,
    kind        text not null check (kind in ('direct', 'ai')),
    name        text not null check (length(name) between 1 and 120),
    question    text not null default '',
    tool        text,
    args        jsonb not null default '{}',
    expect      jsonb not null default '{}',
    as_user_id  bigint references users (id) on delete set null,
    created_by  bigint references users (id) on delete set null,
    created_at  timestamptz not null default now()
);
create index dev_mcp_testcases_endpoint_idx on dev_mcp_testcases (endpoint);

create table dev_mcp_test_runs (
    id           bigserial primary key,
    testcase_id  bigint not null references dev_mcp_testcases (id) on delete cascade,
    version_id   bigint references dev_artifact_versions (id) on delete set null,
    passed       boolean not null,
    detail       jsonb not null default '{}',
    ran_by       bigint references users (id) on delete set null,
    ran_at       timestamptz not null default now()
);
create index dev_mcp_test_runs_case_idx on dev_mcp_test_runs (testcase_id, id desc);

create table dev_call_metrics (
    scope          text not null,
    minute         timestamptz not null,
    calls          integer not null default 0,
    errors         integer not null default 0,
    p50_ms         integer,
    p95_ms         integer,
    cache_hits     integer not null default 0,
    cache_lookups  integer not null default 0,
    primary key (scope, minute)
);

create table dev_metric_alerts (
    scope      text not null,
    kind       text not null,
    raised_at  timestamptz not null default now(),
    detail     text not null default '',
    primary key (scope, kind, raised_at)
);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('developer.metrics', 'developer.metrics',
     'Developer: samler MCP- og API-kald i minut-aggregater og sender alarmer ved høj fejlrate eller usædvanlig brug', 60);

insert into retention_policies (key, title, description, days, min_days) values
    ('dev_call_metrics', 'Kaldsmålinger i Developer', 'Minut-aggregater for MCP-endpoints og API''er (og sendte alarmer)', 90, 7),
    ('dev_mcp_test_runs', 'Testkørsler i MCP-studiet', 'Resultater af testcases', 180, 14);
