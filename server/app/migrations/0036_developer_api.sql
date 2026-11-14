-- Gær Developer, fase 6 (specs/developer/API.md): API-studiet på /gaer/v<major>/<slug>, API-nøgler, udfasning af
-- gamle majors, API-loggen, og nøglepar til eksport og import mellem instanser.
--
-- dev_api_keys        en nøgle er et synligt id og en lang hemmelighed, som kun gemmes som hash (API-7). Scopes pr.
--                     operation ("GET /lejemaal*"), udløb, IP-begrænsning, status (aktiv, roteres, tilbagekaldt).
--                     Typen client_credentials bruges med /gaer/oauth/token (API-6).
-- dev_api_tokens      kortlivede access tokens fra client credentials (kun hash).
-- dev_api_majors      hver major af et API kører videre efter en ny major, indtil sunset_at (API-9).
-- dev_api_log         hvert kald: API, major, operation, nøgle eller bruger, status, tid, cache (API-11).
-- dev_instance_keys   instansens nøglepar til eksportfiler: X25519 til kryptering, Ed25519 til signering. Private nøgler
--                     er krypteret med instansens nøgleudbyder (secrets_box) og kan ikke eksporteres.
-- dev_known_instances modtageres offentlige nøgler; admin bekræfter fingeraftrykket ad en anden kanal.
-- dev_imports         prøver og gennemførte importer (opsummering, aldrig hemmeligheder).

create table dev_api_keys (
    id               bigserial primary key,
    key_id           text not null unique check (key_id ~ '^gk_[a-z0-9]{12,32}$'),
    secret_hash      text not null,
    name             text not null check (length(name) between 1 and 120),
    kind             text not null default 'api_key' check (kind in ('api_key', 'client_credentials')),
    api_artifact_id  bigint references dev_artifacts (id) on delete cascade,
    artifact_id      bigint references dev_artifacts (id) on delete set null,
    scopes           text[] not null default '{}',
    ip_allow         cidr[] not null default '{}',
    expires_at       timestamptz not null,
    status           text not null default 'active' check (status in ('active', 'rotating', 'revoked')),
    rotated_from     bigint references dev_api_keys (id) on delete set null,
    created_by       bigint references users (id) on delete set null,
    created_at       timestamptz not null default now(),
    last_used_at     timestamptz,
    revoked_at       timestamptz
);
create index dev_api_keys_api_idx on dev_api_keys (api_artifact_id);

create table dev_api_tokens (
    token_hash  text primary key,
    key_id      bigint not null references dev_api_keys (id) on delete cascade,
    scopes      text[] not null default '{}',
    expires_at  timestamptz not null
);

create table dev_api_majors (
    api_artifact_id  bigint not null references dev_artifacts (id) on delete cascade,
    major            integer not null,
    version_id       bigint references dev_artifact_versions (id) on delete set null,
    published_at     timestamptz not null default now(),
    sunset_at        timestamptz,
    notified         integer[] not null default '{}',     -- days before sunset already announced (90, 30, 7)
    primary key (api_artifact_id, major)
);

create table dev_api_log (
    id          bigserial primary key,
    ts          timestamptz not null default now(),
    api         text not null,
    major       integer not null,
    method      text not null,
    path        text not null,          -- the template, never the concrete path with ids
    key_id      text,
    user_id     bigint references users (id) on delete set null,
    status      integer not null,
    duration_ms integer,
    cache       text
);
create index dev_api_log_ts_idx on dev_api_log (ts);
create index dev_api_log_api_idx on dev_api_log (api, ts desc);

create table dev_instance_keys (
    id           text primary key,
    kind         text not null check (kind in ('sign', 'encrypt')),
    public_key   text not null,
    private_enc  bytea not null,
    created_at   timestamptz not null default now(),
    retired_at   timestamptz
);

create table dev_known_instances (
    id           bigserial primary key,
    name         text not null check (length(name) between 1 and 120),
    sign_key     text not null,
    encrypt_key  text,
    fingerprint  text not null,
    added_by     bigint references users (id) on delete set null,
    created_at   timestamptz not null default now()
);

create table dev_imports (
    id          bigserial primary key,
    file_hash   text not null,
    signer      text,
    summary     jsonb not null default '{}',
    status      text not null check (status in ('checked', 'imported', 'blocked')),
    created_by  bigint references users (id) on delete set null,
    created_at  timestamptz not null default now()
);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('developer.api_sunset', 'developer.api_sunset',
     'Developer: varsler nøgleejere 90, 30 og 7 dage før en gammel API-major lukker', 86400);

insert into retention_policies (key, title, description, days, min_days) values
    ('dev_api_log', 'API-kald i Developer', 'Loggen over kald til /gaer/v<major>/<slug> (aldrig svar eller nøgler)', 90, 7);

-- The read-only MCP role never sees key hashes, tokens or the instance's private keys.
create or replace function gaer_apply_grants() returns void language plpgsql as $$
declare
    app_role text := current_database() || '_app';
    purge_role text := current_database() || '_purge';
    ro_role text := current_database() || '_mcp_ro';
    log_tables text[] := array['auth_log', 'audit_log', 'feature_usage', 'mcp_log'];
    ro_hidden text[] := array['otp_codes', 'webauthn_challenges', 'sessions', 'mcp_tokens', 'auth_log', 'totp_factors',
                                 'secret_versions', 'dev_api_keys', 'dev_api_tokens', 'dev_instance_keys',
                                 'dev_cache', 'dev_idempotency'];
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
