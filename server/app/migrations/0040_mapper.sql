-- Mapper (specs/mapper/): a personal, hierarchical store of shortcuts in the browser extension (/extension/mapper).
-- Built on Træstrukturer (0022, 0023): the folders are the user's private nodes under ~/mapper, a shortcut is a URL
-- object (meta_objects, kind 'url') tagged with one or more of those folders. The tables below only hold what is the
-- user's own on top of that: their title for a shortcut, pinned folders, and saved views of the lists.

-- A meta object is shared by everyone who tagged the same URL, and its title belongs to whoever created it. When
-- another user saves the same URL with their own title, Mapper keeps that title here and shows it to them only.
create table mapper_titles (
    user_id     bigint not null references users (id) on delete cascade,
    object_id   bigint not null references meta_objects (id) on delete cascade,
    title       text not null,
    updated_at  timestamptz not null default now(),
    primary key (user_id, object_id)
);
create index mapper_titles_object_idx on mapper_titles (object_id);

-- Folders pinned to the top of the tree ("Fastgjort"). Only the user's own private nodes.
create table mapper_pins (
    user_id     bigint not null references users (id) on delete cascade,
    node_id     bigint not null references meta_nodes (id) on delete cascade,
    created_at  timestamptz not null default now(),
    primary key (user_id, node_id)
);
create index mapper_pins_node_idx on mapper_pins (node_id);

-- Saved views of a list (after Baker's ObjectTable in Surdej): layout + sort + visible columns + filter.
-- shared = false: "Mine visninger" (only the owner); shared = true: "Delt med teamet" (every user reads it, the owner
-- edits it, a meta admin may remove it).
create table mapper_views (
    id          bigserial primary key,
    owner_id    bigint not null references users (id) on delete cascade,
    name        text not null check (length(name) between 1 and 60),
    shared      boolean not null default false,
    spec        jsonb not null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index mapper_views_owner_idx on mapper_views (owner_id);
create index mapper_views_shared_idx on mapper_views (shared) where shared;

-- New features start in ring 1 (Internt); /admin/features moves it out.
insert into features (key, title, description, area, ring, builtin) values
    ('mapper', 'Mapper', 'Egne mapper med genveje i sidepanelet: træ, visninger, forhåndsvisning og import af favoritter',
     'integration', 1, true)
on conflict (key) do nothing;

-- Grants are re-applied by gaer_apply_grants() after the run.
