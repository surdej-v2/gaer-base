-- Gær Developer, fase 1 (specs/developer/): artefakter med ejere (RACI), semantiske versioner og godkendelse, et
-- datakatalog med persondata og lineage, og to nye admin-roller.
--
-- dev_artifacts            alt, der defineres i et studie: forbindelser, datasæt, pipelines, operationer,
--                          multisource-kilder, MCP-endpoints, API'er og API-nøgler (F-1). Status aktiv, deaktiveret
--                          eller "kræver review" (F-6). approval_flow: godkendelsesflowet er opt-in pr. artefakt (F-4).
-- dev_artifact_versions    hver version: semver, ændringstype (systemets vurdering, F-2), status, definition (JSON),
--                          kontrakt (det, der sammenlignes) og listen over ændringer.
-- dev_artifact_roles       RACI pr. artefakt: præcis én A, mindst én R, nul eller flere C og I.
-- dev_artifact_approvals   godkendelser pr. version: hvem, i hvilken rolle, svar og tidspunkt (F-3).
-- dev_artifact_dependencies hvad en udgivet version bruger (til impact-analyse, F-6).
-- dev_reviews              "kræver review": et artefakt, der bruger noget, som fik en major.
-- dev_notifications        beskeder i appen til ejere (Informed, godkendere, review). Personlige → GDPR.
--
-- Roller: 'udvikler' og 'udvikler_admin' er admin-roller (app/admin_roles.py) — den mindst invasive vej i den
-- eksisterende model. Eksterne brugere kan aldrig have dem (triggeren nedenfor).

alter table users drop constraint users_admin_roles_check;
alter table users add constraint users_admin_roles_check
    check (admin_roles <@ array['fuld', 'support', 'indhold', 'produkt', 'data', 'privatliv', 'drift', 'udvikler',
                                'udvikler_admin']::text[]);

create function gaer_developer_roles_internal() returns trigger language plpgsql as $$
begin
    if new.audience = 'external' and new.admin_roles && array['udvikler', 'udvikler_admin']::text[] then
        raise exception 'Eksterne brugere kan ikke have rollerne Udvikler eller Udvikler-admin'
            using errcode = 'check_violation';
    end if;
    return new;
end $$;

create trigger users_developer_roles before insert or update of audience, admin_roles on users
    for each row execute function gaer_developer_roles_internal();

create table dev_artifacts (
    id                  bigserial primary key,
    type                text not null check (type in ('connection', 'dataset', 'pipeline', 'operation', 'multisource',
                                                      'mcp_endpoint', 'api', 'api_key')),
    slug                text not null check (slug ~ '^[a-z][a-z0-9_-]{0,62}$'),
    name                text not null check (length(name) between 1 and 120),
    description         text not null default '' check (length(description) <= 4000),
    status              text not null default 'active' check (status in ('active', 'disabled', 'review_required')),
    approval_flow       boolean not null default false,
    current_version_id  bigint,
    created_by          bigint references users (id) on delete set null,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (type, slug)
);

create table dev_artifact_versions (
    id            bigserial primary key,
    artifact_id   bigint not null references dev_artifacts (id) on delete cascade,
    major         integer not null,
    minor         integer not null,
    patch         integer not null,
    change_type   text not null check (change_type in ('initial', 'patch', 'minor', 'major')),
    status        text not null check (status in ('draft', 'pending', 'published', 'superseded', 'rejected', 'withdrawn')),
    definition    jsonb not null,
    contract      jsonb not null,
    changes       jsonb not null default '[]',
    note          text not null default '' check (length(note) <= 2000),
    reason        text not null default '' check (length(reason) <= 2000),   -- begrundelse (nedklassificering)
    created_by    bigint references users (id) on delete set null,
    created_at    timestamptz not null default now(),
    submitted_at  timestamptz,
    decided_at    timestamptz,
    published_at  timestamptz
);
create index dev_artifact_versions_artifact_idx on dev_artifact_versions (artifact_id, id desc);
create unique index dev_artifact_versions_one_draft on dev_artifact_versions (artifact_id) where status in ('draft', 'pending');

alter table dev_artifacts add constraint dev_artifacts_current_fkey
    foreign key (current_version_id) references dev_artifact_versions (id) on delete set null;

create table dev_artifact_roles (
    artifact_id  bigint not null references dev_artifacts (id) on delete cascade,
    user_id      bigint not null references users (id) on delete cascade,
    role         text not null check (role in ('R', 'A', 'C', 'I')),
    primary key (artifact_id, user_id, role)
);
create index dev_artifact_roles_user_idx on dev_artifact_roles (user_id);

create table dev_artifact_approvals (
    id          bigserial primary key,
    version_id  bigint not null references dev_artifact_versions (id) on delete cascade,
    user_id     bigint not null references users (id) on delete cascade,
    role        text not null check (role in ('R', 'A', 'C')),
    decision    text not null default 'pending' check (decision in ('pending', 'approved', 'rejected')),
    note        text not null default '' check (length(note) <= 2000),
    decided_at  timestamptz,
    unique (version_id, user_id, role)
);
create index dev_artifact_approvals_open_idx on dev_artifact_approvals (user_id) where decision = 'pending';

create table dev_artifact_dependencies (
    version_id   bigint not null references dev_artifact_versions (id) on delete cascade,
    depends_on   bigint not null references dev_artifacts (id) on delete cascade,
    detail       text not null default '',
    primary key (version_id, depends_on)
);
create index dev_artifact_dependencies_target_idx on dev_artifact_dependencies (depends_on);

create table dev_reviews (
    id                bigserial primary key,
    artifact_id       bigint not null references dev_artifacts (id) on delete cascade,     -- det berørte artefakt
    cause_artifact_id bigint not null references dev_artifacts (id) on delete cascade,     -- det, der fik en major
    cause_version_id  bigint references dev_artifact_versions (id) on delete set null,
    status            text not null default 'open' check (status in ('open', 'approved')),
    impact            jsonb not null default '{}',
    ai_analysis       text,
    created_at        timestamptz not null default now(),
    decided_by        bigint references users (id) on delete set null,
    decided_at        timestamptz,
    note              text not null default ''
);
create index dev_reviews_open_idx on dev_reviews (artifact_id) where status = 'open';

create table dev_notifications (
    id           bigserial primary key,
    user_id      bigint not null references users (id) on delete cascade,
    kind         text not null,
    title        text not null,
    body         text not null default '',
    link         text not null default '',
    artifact_id  bigint references dev_artifacts (id) on delete cascade,
    created_at   timestamptz not null default now(),
    read_at      timestamptz,
    mailed_at    timestamptz
);
create index dev_notifications_user_idx on dev_notifications (user_id, created_at desc);

-- Datakataloget (F-7, F-9): persondata og følsomme oplysninger pr. felt, en valgt maskering, og lineage.
alter table datasets
    add column kind text not null default 'table' check (kind in ('table', 'api', 'snapshot', 'foreign', 'live')),
    add column artifact_id bigint references dev_artifacts (id) on delete set null,
    add column pipeline_artifact_id bigint references dev_artifacts (id) on delete set null,
    add column refreshed_at timestamptz,
    add column generation bigint not null default 0;
update datasets set kind = 'api' where source_id is not null;

alter table dataset_fields
    add column personal_data boolean not null default false,
    add column sensitive boolean not null default false,
    add column mask text check (mask in ('show', 'round', 'interval', 'aggregate', 'remove', 'mask', 'hash', 'partial')),
    add column inherited boolean not null default false;

create table dataset_lineage (
    id               bigserial primary key,
    field_id         bigint not null references dataset_fields (id) on delete cascade,
    source_field_id  bigint references dataset_fields (id) on delete set null,
    source_label     text not null,          -- datasæt.felt, også når kildefeltet senere er væk
    step             text not null default '',
    transform        text not null default ''
);
create index dataset_lineage_field_idx on dataset_lineage (field_id);

insert into retention_policies (key, title, description, days, min_days) values
    ('dev_notifications', 'Beskeder i Developer', 'Læste beskeder til ejere og godkendere i /developer', 90, 7);

-- AI-forslag og impact-analyser i /developer tæller som AI-forbrug.
alter table ai_usage drop constraint if exists ai_usage_surface_check;
alter table ai_usage add constraint ai_usage_surface_check
    check (surface in ('app', 'extension', 'word', 'outlook', 'document', 'mobile', 'knowledge', 'developer'));

-- Grants are re-applied by gaer_apply_grants() after the run.
