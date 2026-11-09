-- Viden (specs/knowledge/README.md): vidensrum with uploaded documents, their extracted text and searchable chunks,
-- and no-code agents that answer from them on /extension/agent/:slug.
--
-- knowledge_spaces          a curated collection of documents around one topic; deleted_at = in the bin
-- knowledge_documents       one uploaded file; the bytes live in Object Storage under storage_key
-- knowledge_texts           the full extracted Markdown of a document (what was indexed)
-- knowledge_chunks          searchable pieces: full text (tsv) always, a unit-length embedding when a model is set up
-- knowledge_ingest_log      append-only processing log per space and document (kept by retention policy)
-- knowledge_agents          the working copy of an agent
-- knowledge_agent_versions  immutable snapshot per publish; users chat with the latest one
-- knowledge_agent_members   who may use (or manage) an agent whose access is 'members'
-- knowledge_conversations   a user's conversations with an agent (personal data: GDPR export, cascade on erasure)
-- knowledge_messages        the messages, with the citations of each answer

create table knowledge_spaces (
    id           bigserial primary key,
    name         text not null check (length(name) between 1 and 200),
    description  text not null default '',
    created_by   bigint references users (id) on delete set null,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    deleted_at   timestamptz,
    deleted_by   bigint references users (id) on delete set null
);
create index knowledge_spaces_deleted_idx on knowledge_spaces (deleted_at);

create table knowledge_documents (
    id            bigserial primary key,
    space_id      bigint not null references knowledge_spaces (id) on delete cascade,
    filename      text not null,
    path          text not null default '',          -- path inside the uploaded ZIP, or the file name
    content_type  text not null default 'application/octet-stream',
    size_bytes    bigint not null default 0,
    storage_key   text not null unique,              -- knowledge/<uuid>; never contains user input
    sha256        text not null,
    status        text not null default 'pending'
                  check (status in ('pending', 'processing', 'indexed', 'skipped', 'failed', 'error')),
    error         text not null default '',
    page_count    integer not null default 0,
    chunk_count   integer not null default 0,
    char_count    integer not null default 0,
    embedded      boolean not null default false,
    uploaded_by   bigint references users (id) on delete set null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    indexed_at    timestamptz
);
create index knowledge_documents_space_idx on knowledge_documents (space_id, path);

create table knowledge_texts (
    document_id  bigint primary key references knowledge_documents (id) on delete cascade,
    markdown     text not null
);

create table knowledge_chunks (
    id           bigserial primary key,
    document_id  bigint not null references knowledge_documents (id) on delete cascade,
    space_id     bigint not null references knowledge_spaces (id) on delete cascade,
    chunk_index  integer not null,
    section      text not null default '',
    page         integer,
    content      text not null,
    tokens       integer not null default 0,
    tsv          tsvector generated always as (to_tsvector('simple', section || ' ' || content)) stored,
    embedding    real[],                              -- unit length, so cosine similarity is a dot product
    embed_model  text,
    unique (document_id, chunk_index)
);
create index knowledge_chunks_space_idx on knowledge_chunks (space_id);
create index knowledge_chunks_tsv_idx on knowledge_chunks using gin (tsv);

create table knowledge_ingest_log (
    id           bigserial primary key,
    space_id     bigint not null references knowledge_spaces (id) on delete cascade,
    document_id  bigint references knowledge_documents (id) on delete cascade,
    level        text not null default 'info' check (level in ('info', 'warn', 'error')),
    stage        text not null,                       -- queued, extract, chunk, embed, done, retry, skipped, failed
    message      text not null,
    created_at   timestamptz not null default now()
);
create index knowledge_ingest_log_space_idx on knowledge_ingest_log (space_id, created_at desc);

create table knowledge_agents (
    id                 bigserial primary key,
    slug               text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{0,59}$'),
    name               text not null check (length(name) between 1 and 120),
    description        text not null default '',
    instructions       text not null default '',
    welcome            text not null default '',
    suggested_prompts  text[] not null default '{}',
    space_ids          bigint[] not null default '{}',
    access             text not null default 'all' check (access in ('all', 'members')),
    status             text not null default 'draft' check (status in ('draft', 'published')),
    version_major      integer not null default 0,      -- 0.0 = never published
    version_minor      integer not null default 0,
    created_by         bigint references users (id) on delete set null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create table knowledge_agent_versions (
    id          bigserial primary key,
    agent_id    bigint not null references knowledge_agents (id) on delete cascade,
    major       integer not null,
    minor       integer not null,
    snapshot    jsonb not null,                       -- name, description, instructions, welcome, prompts, space_ids
    note        text not null default '',
    created_by  bigint references users (id) on delete set null,
    created_at  timestamptz not null default now(),
    unique (agent_id, major, minor)
);

create table knowledge_agent_members (
    agent_id    bigint not null references knowledge_agents (id) on delete cascade,
    user_id     bigint not null references users (id) on delete cascade,
    privilege   text not null default 'use' check (privilege in ('use', 'manage')),
    added_by    bigint references users (id) on delete set null,
    created_at  timestamptz not null default now(),
    primary key (agent_id, user_id)
);
create index knowledge_agent_members_user_idx on knowledge_agent_members (user_id);

create table knowledge_conversations (
    id          bigserial primary key,
    agent_id    bigint not null references knowledge_agents (id) on delete cascade,
    user_id     bigint not null references users (id) on delete cascade,
    title       text not null default '',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index knowledge_conversations_user_idx on knowledge_conversations (user_id, agent_id, updated_at desc);

create table knowledge_messages (
    id               bigserial primary key,
    conversation_id  bigint not null references knowledge_conversations (id) on delete cascade,
    role             text not null check (role in ('user', 'agent')),
    text             text not null,
    citations        jsonb not null default '[]',
    agent_version    text not null default '',
    created_at       timestamptz not null default now()
);
create index knowledge_messages_conversation_idx on knowledge_messages (conversation_id, id);

-- AI usage from agents counts in the same monthly limit as the AI chat.
alter table ai_usage drop constraint if exists ai_usage_surface_check;
alter table ai_usage add constraint ai_usage_surface_check
    check (surface in ('app', 'extension', 'word', 'outlook', 'document', 'mobile', 'knowledge'));

insert into retention_policies (key, title, description, days, min_days) values
    ('knowledge_ingest_log', 'Viden: behandlingslog', 'Logposter fra indeksering af dokumenter', 30, 1),
    ('knowledge_conversations', 'Viden: samtaler', 'Samtaler med agenter uden nye beskeder i N dage', 180, 7);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('knowledge.bin', 'knowledge.bin', 'Sletter vidensrum, der har ligget i papirkurven i 30 dage, med deres filer', 86400);
