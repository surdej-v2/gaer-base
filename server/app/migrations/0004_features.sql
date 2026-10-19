-- Features and rings (from happy-groups member-features, without groups). The ring is a maturity axis:
-- everything is deployed everywhere, the ring decides who sees it. A feature is shown to a user when
-- feature.ring >= the user's level (feature_rings.ring, default 4). Rings are set in /admin/features only.

create table features (
    key          text primary key check (key ~ '^[a-z0-9][a-z0-9-]*$'),
    title        text not null,
    description  text not null default '',
    area         text not null check (area in ('bruger', 'integration', 'admin', 'platform')),
    ring         integer not null default 1 check (ring between 1 and 4),
    observe      boolean not null default false,
    builtin      boolean not null default false,             -- from the code catalog (app/features_catalog.py)
    created_by   bigint references users (id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- A user's own level ("Dit niveau"). No row means ring 4: only stable features plus preview.
create table feature_rings (
    user_id     bigint primary key references users (id) on delete cascade,
    ring        integer not null check (ring between 1 and 4),
    set_by      bigint references users (id) on delete set null,
    updated_at  timestamptz not null default now()
);

-- Usage of a feature by a user (append-only, like the other logs). No FK: deleted with analytics
-- retention and by the purge user on erasure.
create table feature_usage (
    id           bigserial primary key,
    ts           timestamptz not null default now(),
    feature_key  text not null,
    user_id      bigint
);
create index feature_usage_key_idx on feature_usage (feature_key, ts desc);
create index feature_usage_user_idx on feature_usage (user_id, ts desc);
create trigger feature_usage_no_update before update on feature_usage
    for each row execute function gaer_reject_log_update();

-- Everything gaer-base ships with is stable. Features added to the catalog later are imported in ring 1.
insert into features (key, title, description, area, ring, builtin) values
    ('klip', 'Klip', 'Gem tekst og links som klip og brug dem i Office og udvidelsen', 'bruger', 4, true),
    ('filer', 'Filer', 'Upload til Object Storage, download via links der udløber', 'bruger', 4, true),
    ('passkey', 'Passkey', 'Passkey efter tre logins med engangskode', 'bruger', 4, true),
    ('mobil', 'Mobil-app', 'PWA under /m med bundmenu, kamera-upload og klip', 'bruger', 4, true),
    ('extension', 'Browserudvidelse', 'Sidepanel i Chrome og Edge, gem siden som klip', 'integration', 4, true),
    ('office', 'Office-tilføjelser', 'Word, Excel, PowerPoint og Outlook i én router', 'integration', 4, true),
    ('website', 'Website /w', 'Offentlige Markdown-sider på dansk og engelsk', 'platform', 4, true),
    ('heatmaps', 'Heatmaps', 'Klik- og scroll-heatmaps pr. side', 'admin', 4, true),
    ('gdpr-export', 'GDPR-eksport', 'Samlet eksport ved anmodning om indsigt', 'admin', 4, true),
    ('gaer-sync', 'Hent opdateringer', 'Træk ændringer fra gaer-base ind i din fork', 'platform', 4, true);

-- New version of the grants: feature_usage is a log table (app: select/insert; purge: select/delete).
create or replace function gaer_apply_grants() returns void language plpgsql as $$
declare
    app_role text := current_database() || '_app';
    purge_role text := current_database() || '_purge';
    log_tables text[] := array['auth_log', 'audit_log', 'feature_usage'];
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
        execute format('grant select, delete on auth_log, audit_log, analytics_events, consents, feature_usage to %I', purge_role);
        execute format('grant insert on audit_log to %I', purge_role);
        execute format('grant usage on sequence audit_log_id_seq to %I', purge_role);
        execute format('grant select on users, settings to %I', purge_role);
    end if;
end $$;
