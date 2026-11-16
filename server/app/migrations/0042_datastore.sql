-- Datalager (/admin/data/store, /api/store): NoSQL collections (JSON documents) and KV namespaces (key → JSON value).
-- app/datastore.py holds the rules; routes/datastore.py (/api/store) and routes/admin_datastore.py (/api/admin/store)
-- the API.
--
-- A container is a collection (kind 'nosql') or a namespace (kind 'kv'). It is owned either by a group (with its own
-- RACI: exactly one A, at least one R, like the secret manager) or by one user (personal, "~/<slug>"). It is private
-- (only the owners and the owning group) or shared (groups in store_shares get read or write). A single item can be
-- private even in a shared container. Versioning is 'none', 'revisions' (each write is a revision that can be
-- restored) or 'semver': the items are the working copy; a release snapshots it as major.minor, approved by the RACI
-- (minor_rule / major_rule), and readers get the current release or pin a major ("kunder@3").

create table store_containers (
    id                  bigserial primary key,
    kind                text not null check (kind in ('nosql', 'kv')),
    slug                text not null check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name                text not null check (length(name) between 1 and 120),
    description         text not null default '' check (length(description) <= 1000),
    group_id            bigint references groups (id) on delete restrict,
    owner_id            bigint references users (id) on delete cascade,
    visibility          text not null default 'private' check (visibility in ('private', 'shared')),
    versioning          text not null default 'revisions' check (versioning in ('none', 'revisions', 'semver')),
    -- auto: an R's release is approved at once · one_r: one other R or A · a: the A
    minor_rule          text not null default 'one_r' check (minor_rule in ('auto', 'one_r', 'a')),
    -- a_and_all_c: A and every C · a_and_one_c: A and one C · a_only: A
    major_rule          text not null default 'a_and_all_c' check (major_rule in ('a_and_all_c', 'a_and_one_c', 'a_only')),
    sunset_days         integer not null default 90 check (sunset_days between 0 and 3650),
    json_schema         jsonb,                                -- nosql: the working schema (a subset of JSON Schema)
    default_ttl_seconds integer check (default_ttl_seconds between 1 and 31536000),  -- kv: new keys expire after
    current_major       integer,
    current_minor       integer,
    current_release_id  bigint,
    created_by          bigint references users (id) on delete set null,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    archived_at         timestamptz,
    check (num_nonnulls(group_id, owner_id) = 1),
    check (json_schema is null or kind = 'nosql'),
    check (default_ttl_seconds is null or kind = 'kv')
);
create unique index store_containers_group_slug_idx on store_containers (slug) where group_id is not null and archived_at is null;
create unique index store_containers_owner_slug_idx on store_containers (owner_id, slug) where owner_id is not null and archived_at is null;
create index store_containers_group_idx on store_containers (group_id);

create table store_raci (
    container_id    bigint not null references store_containers (id) on delete cascade,
    user_id         bigint not null references users (id) on delete cascade,
    role            text not null check (role in ('R', 'A', 'C', 'I')),
    added_at        timestamptz not null default now(),
    primary key (container_id, user_id, role)
);
create unique index store_raci_one_a_idx on store_raci (container_id) where role = 'A';
create index store_raci_user_idx on store_raci (user_id);

-- Groups a shared container is shared with. Has no effect while the container is private.
create table store_shares (
    container_id    bigint not null references store_containers (id) on delete cascade,
    group_id        bigint not null references groups (id) on delete cascade,
    level           text not null check (level in ('read', 'write')),
    added_by        bigint references users (id) on delete set null,
    added_at        timestamptz not null default now(),
    primary key (container_id, group_id)
);
create index store_shares_group_idx on store_shares (group_id);

-- The working copy: a document (nosql, key = its id) or a key (kv).
create table store_items (
    id              bigserial primary key,
    container_id    bigint not null references store_containers (id) on delete cascade,
    key             text not null check (key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
    value           jsonb not null,
    private         boolean not null default false,       -- only R, A (or the owner) see it, even when shared
    rev             integer not null default 1,
    expires_at      timestamptz,                          -- kv: gone after this (job store.maintenance)
    created_by      bigint references users (id) on delete set null,
    updated_by      bigint references users (id) on delete set null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (container_id, key)
);
create index store_items_value_idx on store_items using gin (value jsonb_path_ops);
create index store_items_expires_idx on store_items (expires_at) where expires_at is not null;

-- History of the working copy (versioning 'revisions' and 'semver'). value null = the item was deleted.
create table store_revisions (
    id              bigserial primary key,
    container_id    bigint not null references store_containers (id) on delete cascade,
    item_key        text not null,
    rev             integer not null,
    value           jsonb,
    private         boolean not null default false,
    changed_by      bigint references users (id) on delete set null,
    changed_at      timestamptz not null default now(),
    superseded_at   timestamptz,                          -- a newer revision replaced it; purged after the policy
    unique (container_id, item_key, rev)
);
create index store_revisions_superseded_idx on store_revisions (superseded_at) where superseded_at is not null;

create table store_releases (
    id              bigserial primary key,
    container_id    bigint not null references store_containers (id) on delete cascade,
    major           integer not null check (major >= 1),
    minor           integer not null check (minor >= 0),
    kind            text not null check (kind in ('major', 'minor')),
    status          text not null default 'pending'
                    check (status in ('pending', 'approved', 'rejected', 'cancelled', 'expired')),
    approval_rule   text not null,                        -- the container's rule when proposed
    fallback        boolean not null default false,       -- nobody else could approve: one other admin with the section
    note            text not null default '' check (length(note) <= 2000),
    breaking        jsonb not null default '[]',          -- why it must be a major (texts)
    changes         jsonb not null default '{}',          -- added / changed / removed keys against the current release
    json_schema     jsonb,
    item_count      integer not null default 0,
    proposed_by     bigint references users (id) on delete set null,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    decided_at      timestamptz,
    sunset_at       timestamptz,                          -- an older major: readable until then
    items_purged_at timestamptz
);
create unique index store_releases_one_pending_idx on store_releases (container_id) where status = 'pending';
create unique index store_releases_version_idx on store_releases (container_id, major, minor) where status = 'approved';
create index store_releases_pending_idx on store_releases (expires_at) where status = 'pending';

alter table store_containers add constraint store_containers_release_fk
    foreign key (current_release_id) references store_releases (id) on delete set null;

-- The snapshot a release holds: what readers of that version get.
create table store_release_items (
    release_id      bigint not null references store_releases (id) on delete cascade,
    key             text not null,
    value           jsonb not null,
    private         boolean not null default false,
    primary key (release_id, key)
);
create index store_release_items_value_idx on store_release_items using gin (value jsonb_path_ops);

-- One row per person who must answer, fixed when the release is proposed (fallback: the admin who answered).
create table store_release_approvals (
    release_id      bigint not null references store_releases (id) on delete cascade,
    approver_id     bigint not null references users (id) on delete cascade,
    roles           text[] not null,
    decision        text not null default 'pending' check (decision in ('pending', 'approved', 'rejected')),
    note            text check (length(note) <= 500),
    decided_at      timestamptz,
    primary key (release_id, approver_id)
);
create index store_release_approvals_pending_idx on store_release_approvals (approver_id) where decision = 'pending';

insert into job_schedules (name, kind, description, interval_seconds) values
    ('store.maintenance', 'store.maintenance',
     'Datalager: udløber ventende versioner, sletter udløbne nøgler og snapshots af udfasede majors', 3600);

insert into retention_policies (key, title, description, days, min_days) values
    ('store_revisions', 'Gamle revisioner i datalageret',
     'Revisioner af dokumenter og nøgler, der er erstattet af en nyere for mere end N dage siden', 365, 7),
    ('store_releases', 'Afviste og udløbne versionsforslag i datalageret',
     'Forslag til versioner, der blev afvist, annulleret eller udløb for mere end N dage siden (audit-loggen beholder sporet)', 180, 30);
