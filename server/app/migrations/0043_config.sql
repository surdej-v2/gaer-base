-- Konfiguration (/admin/config): settings an admin may change instead of an environment variable.
--
-- config_values    one row per field in app/config_registry.py that is set here; the value is JSON of the field's
--                  type. A row wins over the environment; no row = the environment, then the default in app/config.py.
-- config_secrets   API keys and tokens (mail providers, OVH SMS, AI): encrypted with app.secrets_box (purpose
--                  "config:<key>"), write-only from /admin. bound_host is the host of the endpoint the secret was
--                  given for (AI_BASE_URL, OVH_ENDPOINT, LETTERMINT_API_URL); a secret is only used against that
--                  host, so changing an endpoint cannot send a stored key somewhere new.
-- Root keys, database URLs, storage and the switches that must never be set from a browser stay in the environment
-- (config_registry.ENV_ONLY). Every change is audited in the same transaction (config.changed).

create table config_values (
    key         text primary key check (key ~ '^[a-z][a-z0-9_]{1,63}$'),
    value       jsonb not null,
    updated_at  timestamptz not null default now(),
    updated_by  bigint references users (id) on delete set null
);

create table config_secrets (
    key         text primary key check (key ~ '^[a-z][a-z0-9_]{1,63}$'),
    value_enc   bytea not null,
    bound_host  text,
    set_at      timestamptz not null default now(),
    set_by      bigint references users (id) on delete set null
);

-- The read-only MCP role (db_query in /mcp/admin) never sees encrypted secrets. 0036 dropped the login-provider
-- tables from this list; they are hidden again here.
create or replace function gaer_apply_grants() returns void language plpgsql as $$
declare
    app_role text := current_database() || '_app';
    purge_role text := current_database() || '_purge';
    ro_role text := current_database() || '_mcp_ro';
    log_tables text[] := array['auth_log', 'audit_log', 'feature_usage', 'mcp_log'];
    ro_hidden text[] := array['otp_codes', 'webauthn_challenges', 'sessions', 'mcp_tokens', 'auth_log', 'totp_factors',
                                 'secret_versions', 'dev_api_keys', 'dev_api_tokens', 'dev_instance_keys',
                                 'dev_cache', 'dev_idempotency', 'config_secrets', 'login_providers',
                                 'login_flows', 'login_assertions_seen'];
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
