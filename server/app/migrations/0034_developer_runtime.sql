-- Gær Developer, fase 3 (specs/developer/RUNTIME.md): operationer som MCP-tools, endpoints fra Datastudie, og
-- cache, rate limits og idempotens i Postgres, når Redis ikke er sat op (REDIS_URL).
--
-- mcp_tools            en tredje slags tool: 'operation' peger på et operation-artefakt. Den har sit eget niveau
--                      (hvid, grøn, gul, rød; MCP-5) ligesom Code tools. Data tools følger stadig datasættet.
-- mcp_endpoints        artifact_id: et endpoint lavet i Datastudie styres af sit artefakt (versioner, godkendelse);
--                      MCP Studio kan ikke rette det direkte.
-- mcp_log              cache: hit, stale eller miss pr. kald (MCP-14).
-- dev_cache            krypterede værdier (AES-GCM) med udløb; de mindst brugte fjernes først (F-20).
-- dev_cache_generations en generation pr. artefakt: "Ryd cache" og en ny version tæller den op (F-21).
-- dev_rate_buckets     token buckets pr. nøgle (endpoint, bruger, klient, API-nøgle og operation).
-- dev_idempotency      Idempotency-Key med svaret i et døgn (API-4).

alter table mcp_tools drop constraint mcp_tools_kind_check;
alter table mcp_tools drop constraint mcp_tools_check1;
alter table mcp_tools add column artifact_id bigint references dev_artifacts (id) on delete cascade;
alter table mcp_tools add constraint mcp_tools_kind_check check (kind in ('code', 'data', 'operation'));
alter table mcp_tools add constraint mcp_tools_classification_kind_check check ((kind = 'data') = (classification is null));
alter table mcp_tools add constraint mcp_tools_operation_check check ((kind = 'operation') = (artifact_id is not null));
alter table mcp_tools add column examples jsonb not null default '[]';

alter table mcp_endpoints add column artifact_id bigint references dev_artifacts (id) on delete set null;

alter table mcp_log add column cache text check (cache in ('hit', 'stale', 'miss'));

create table dev_cache (
    key          text primary key,
    value        bytea not null,
    stored_at    timestamptz not null default now(),
    expires_at   timestamptz not null,
    last_hit_at  timestamptz not null default now(),
    hits         integer not null default 0
);
create index dev_cache_expires_idx on dev_cache (expires_at);
create index dev_cache_lru_idx on dev_cache (last_hit_at);

create table dev_cache_generations (
    scope       text primary key,
    generation  bigint not null default 0
);

create table dev_rate_buckets (
    key         text primary key,
    tokens      double precision not null,
    updated_at  timestamptz not null default now()
);

create table dev_idempotency (
    key         text primary key,
    status      integer not null,
    body        jsonb,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);
create index dev_idempotency_expires_idx on dev_idempotency (expires_at);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('developer.cache_cleanup', 'developer.cache_cleanup',
     'Developer: fjerner udløbne cacheværdier, de mindst brugte over grænsen, gamle rate limits og idempotensnøgler', 600);
