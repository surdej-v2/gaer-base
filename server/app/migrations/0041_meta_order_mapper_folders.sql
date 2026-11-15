-- Ordering and labels for Træstrukturer, and Mapper's per-folder settings (specs/meta/, specs/mapper/).
--
-- meta_nodes (generic, from Surdej's member-meta: labels per language, childSort per parent, sortKey):
--   labels      other languages of the label, {"en": "Operations"}; the label column stays the Danish one
--   sort_key    a free sort key ("010", "b") used when the parent sorts its children by key
--   child_sort  how the node orders its children: label (alphabetical), sort_key, manual (sort_order);
--               null = the tree's default (sort_order, then label), as before this migration
-- meta_assignments.sort_order: a manual order of the objects tagged with a node (null = by when they were tagged).

alter table meta_nodes
    add column labels jsonb not null default '{}'::jsonb,
    add column sort_key text check (sort_key is null or length(sort_key) between 1 and 64),
    add column child_sort text check (child_sort in ('label', 'sort_key', 'manual'));

alter table meta_assignments add column sort_order integer;

-- Mapper's settings for one of the user's folders (or the root ~/mapper): how its shortcuts are sorted when the view
-- says "Mappens sortering", and the layout it opens in (null = follow the view).
create table mapper_folder_settings (
    node_id        bigint primary key references meta_nodes (id) on delete cascade,
    user_id        bigint not null references users (id) on delete cascade,
    shortcut_sort  text check (shortcut_sort in ('manual', 'name', 'site', 'added')),
    layout         text check (layout in ('list', 'grid', 'table')),
    updated_at     timestamptz not null default now()
);
create index mapper_folder_settings_user_idx on mapper_folder_settings (user_id);

-- Grants are re-applied by gaer_apply_grants() after the run.
