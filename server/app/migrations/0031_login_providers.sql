-- Login-udbydere (/admin/login-providers): log ind med en konto, brugeren allerede har.
--
-- A provider replaces only the one-time code. Every rule after the code still applies: "Kræv passkey" gives a pending
-- session, /admin needs a second factor after the grace logins, and a provider never makes anyone admin.
-- app/login_providers.py holds the rules; routes/login_providers.py the API.
--
-- login_providers         one row per provider: Microsoft Entra ID, Google, GitHub, Amazon (one each) and any number
--                         of SAML 2.0 connections. Off until an admin switches it on. Non-secret settings in config;
--                         the client secret and the SAML SP key are encrypted with app.secrets_box and never leave the server.
-- user_identities         a user's account at a provider (the provider's stable subject: sub, oid, NameID …).
-- login_flows             one started login, link or test: state for OIDC/OAuth, RelayState + request id for SAML.
--                         Bound to the browser by a cookie (only its hash is stored); single use; short-lived.
-- login_assertions_seen   SAML assertion ids already used, so a captured response cannot be replayed.

create table login_providers (
    id                  bigserial primary key,
    key                 text not null unique check (key ~ '^[a-z][a-z0-9-]{1,39}$'),
    kind                text not null check (kind in ('entra', 'google', 'github', 'amazon', 'saml')),
    name                text not null check (length(name) between 1 and 80),
    enabled             boolean not null default false,
    config              jsonb not null default '{}',
    client_secret_enc   bytea,
    client_secret_set_at timestamptz,
    sp_key_enc          bytea,
    sp_cert             text,
    idp_meta            jsonb,
    last_test           jsonb,
    last_login_at       timestamptz,
    created_by          bigint references users (id) on delete set null,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    enabled_at          timestamptz,
    enabled_by          bigint references users (id) on delete set null
);
-- One of each named provider; SAML may have several connections.
create unique index login_providers_kind_idx on login_providers (kind) where kind <> 'saml';

create table user_identities (
    id              bigserial primary key,
    user_id         bigint not null references users (id) on delete cascade,
    provider_id     bigint not null references login_providers (id) on delete cascade,
    subject         text not null check (length(subject) between 1 and 512),
    email           text,
    linked_via      text not null check (linked_via in ('user', 'verified_email', 'signup')),
    created_at      timestamptz not null default now(),
    last_used_at    timestamptz,
    unique (provider_id, subject),
    unique (provider_id, user_id)
);
create index user_identities_user_idx on user_identities (user_id);

create table login_flows (
    id              text primary key,
    provider_id     bigint not null references login_providers (id) on delete cascade,
    purpose         text not null check (purpose in ('login', 'link', 'test')),
    user_id         bigint references users (id) on delete cascade,
    browser_hash    text not null,
    verifier        text,
    nonce           text,
    request_id      text,
    return_to       text,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    consumed_at     timestamptz
);
create index login_flows_expires_idx on login_flows (expires_at);

create table login_assertions_seen (
    provider_id     bigint not null references login_providers (id) on delete cascade,
    assertion_id    text not null,
    expires_at      timestamptz not null,
    primary key (provider_id, assertion_id)
);

-- Memberships a provider's group mapping added (Entra or SAML groups → Gær groups). Only these are removed again when
-- the person leaves the provider's group; memberships an admin added are left alone.
alter table group_members add column via_provider_id bigint references login_providers (id) on delete set null;

insert into retention_policies (key, title, description, days, min_days) values
    ('login_flows', 'Login via udbydere', 'Påbegyndte logins via en login-udbyder og brugte SAML-assertions, efter de er udløbet.', 7, 1)
on conflict (key) do nothing;

insert into job_schedules (name, kind, description, interval_seconds) values
    ('auth.saml_metadata', 'auth.saml_metadata',
     'Henter SAML-metadata for login-udbydere og advarer 30 dage før et certifikat udløber', 86400)
on conflict (name) do nothing;

-- The read-only MCP role (db_query in /mcp/admin) never sees client secrets, SP keys or login flows.
create or replace function gaer_apply_grants() returns void language plpgsql as $$
declare
    app_role text := current_database() || '_app';
    purge_role text := current_database() || '_purge';
    ro_role text := current_database() || '_mcp_ro';
    log_tables text[] := array['auth_log', 'audit_log', 'feature_usage', 'mcp_log'];
    ro_hidden text[] := array['otp_codes', 'webauthn_challenges', 'sessions', 'mcp_tokens', 'auth_log', 'totp_factors',
                                 'secret_versions', 'login_providers', 'login_flows', 'login_assertions_seen'];
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

-- Grants are re-applied by gaer_apply_grants() after the run.
