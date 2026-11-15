-- Gær Developer, fase 2 (specs/developer/DATA.md): dataforbindelser via FDW, snapshots og pipelinekørsler.
--
-- data_sources        en forbindelse er en datakilde (som API-kilderne fra 0012) med en ny type: postgres, mysql,
--                     graph (live) eller delegated (udvidelsespunkt: delegeret adgang bygges i en separat tråd).
--                     Den får dermed hemmeligheder med "Brug i kilde" fra Hemmeligheder uden nye regler (0027).
-- snapshots           schemaet med pipelines' materialized views (PL-6). Standard i denne database;
--                     ANALYTICS_DATABASE_URL flytter det til gaer_analytics uden kodeændringer.
-- ingest.load_status  nattens indlæsning: én række pr. kilde og dag (afsnit 10). Indlæseren skriver den; en trigger
--                     sender NOTIFY load_status, '<kilde>' når en kilde er færdig.
-- dev_pipeline_runs   hver kørsel (PL-11): start, slut, status, rækker, størrelse, fejltekst; faserne i _phases.

alter table data_sources drop constraint data_sources_kind_check;
alter table data_sources add constraint data_sources_kind_check
    check (kind in ('rest', 'postgres', 'mysql', 'graph', 'delegated'));
alter table data_sources add column artifact_id bigint references dev_artifacts (id) on delete set null;

-- postgres_fdw når serveren har den og migrationsrollen må installere den. Ellers bruger forbindelser direkte adgang,
-- og Forbindelser viser, at FDW mangler.
do $$
begin
    if exists (select 1 from pg_available_extensions where name = 'postgres_fdw') then
        begin
            create extension if not exists postgres_fdw;
        exception when insufficient_privilege or feature_not_supported then
            raise notice 'postgres_fdw kunne ikke installeres af %: forbindelser bruger direkte adgang', current_user;
        end;
    end if;
end $$;

create schema if not exists snapshots;
create schema if not exists ingest;

create table ingest.load_status (
    source       text not null,
    load_date    date not null,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,
    status       text not null check (status in ('running', 'succeeded', 'failed')),
    row_count    bigint,
    message      text,
    primary key (source, load_date)
);

create function ingest.notify_load_status() returns trigger language plpgsql as $$
begin
    if new.status = 'succeeded' then
        perform pg_notify('load_status', new.source);
    end if;
    return new;
end $$;
create trigger load_status_notify after insert or update of status on ingest.load_status
    for each row execute function ingest.notify_load_status();

create table dev_pipeline_runs (
    id            bigserial primary key,
    artifact_id   bigint not null references dev_artifacts (id) on delete cascade,
    version_id    bigint references dev_artifact_versions (id) on delete set null,
    run_date      date not null default current_date,
    trigger       text not null check (trigger in ('schedule', 'manual', 'dependency')),
    status        text not null check (status in ('queued', 'waiting', 'running', 'succeeded', 'failed', 'deferred')),
    queued_at     timestamptz not null default now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    rows          bigint,
    size_bytes    bigint,
    small_groups  integer,
    error         text,
    requested_by  bigint references users (id) on delete set null
);
create index dev_pipeline_runs_artifact_idx on dev_pipeline_runs (artifact_id, id desc);
create index dev_pipeline_runs_date_idx on dev_pipeline_runs (run_date, status);

create table dev_pipeline_run_phases (
    id        bigserial primary key,
    run_id    bigint not null references dev_pipeline_runs (id) on delete cascade,
    phase     text not null,
    label     text not null,
    started_at timestamptz not null,
    ms        integer not null,
    detail    text not null default ''
);
create index dev_pipeline_run_phases_run_idx on dev_pipeline_run_phases (run_id);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('pipeline.schedule', 'pipeline.schedule',
     'Pipelines: starter dagens kørsler, når indlæsningen er færdig (tidligst kl. 06), og markerer Udskudt efter kl. 08', 300);

insert into retention_policies (key, title, description, days, min_days) values
    ('dev_pipeline_runs', 'Pipelinekørsler', 'Kørselsloggen i Pipeline (kørsler og tid pr. fase)', 180, 14);

-- Grants: the app (and the read-only MCP role) also get the snapshots and ingest schemas.
create or replace function gaer_apply_grants() returns void language plpgsql as $$
declare
    app_role text := current_database() || '_app';
    purge_role text := current_database() || '_purge';
    ro_role text := current_database() || '_mcp_ro';
    log_tables text[] := array['auth_log', 'audit_log', 'feature_usage', 'mcp_log'];
    ro_hidden text[] := array['otp_codes', 'webauthn_challenges', 'sessions', 'mcp_tokens', 'auth_log', 'totp_factors',
                                 'secret_versions', 'dev_api_keys', 'dev_cache', 'dev_idempotency'];
    t record;
begin
    if exists (select 1 from pg_roles where rolname = app_role) then
        execute format('grant usage on schema public to %I', app_role);
        for t in select tablename from pg_tables where schemaname = 'public' loop
            if t.tablename = any (log_tables) then
                execute format('revoke update, delete, truncate on %I from %I', t.tablename, app_role);
                execute format('grant select, insert on %I to %I', t.tablename, app_role);
            elsif t.tablename = 'schema_migrations' then
                execute format('grant select on %I to %I', t.tablename, app_role);
            else
                execute format('grant select, insert, update, delete on %I to %I', t.tablename, app_role);
            end if;
        end loop;
        execute format('grant usage, select on all sequences in schema public to %I', app_role);
        execute format('grant execute on function gaer_ensure_analytics_partition(date) to %I', app_role);
        if exists (select 1 from pg_namespace where nspname = 'snapshots') then
            execute format('grant usage, create on schema snapshots to %I', app_role);
        end if;
        if exists (select 1 from pg_namespace where nspname = 'ingest') then
            execute format('grant usage on schema ingest to %I', app_role);
            execute format('grant select, insert, update on all tables in schema ingest to %I', app_role);
        end if;
    end if;

    if exists (select 1 from pg_roles where rolname = purge_role) then
        execute format('grant usage on schema public to %I', purge_role);
        execute format('grant select, delete on auth_log, audit_log, analytics_events, consents, feature_usage, mcp_log to %I',
                       purge_role);
        execute format('grant insert on audit_log to %I', purge_role);
        execute format('grant usage on sequence audit_log_id_seq to %I', purge_role);
        execute format('grant select on users, settings to %I', purge_role);
    end if;

    if exists (select 1 from pg_roles where rolname = ro_role) then
        execute format('grant usage on schema public to %I', ro_role);
        for t in select tablename from pg_tables where schemaname = 'public' loop
            if t.tablename = any (ro_hidden) then
                execute format('revoke all on %I from %I', t.tablename, ro_role);
            else
                execute format('grant select on %I to %I', t.tablename, ro_role);
            end if;
        end loop;
    end if;
end $$;
