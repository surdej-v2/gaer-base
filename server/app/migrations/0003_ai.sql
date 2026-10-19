-- Optional AI chat (OVHcloud AI Endpoints or another OpenAI-compatible service).
-- Prompts and answers are not stored. Only usage is recorded, for the monthly limit per user.

alter table users add column ai_monthly_tokens integer;    -- null: the instance default from /admin/ai

create table ai_usage (
    id                 bigserial primary key,
    ts                 timestamptz not null default now(),
    user_id            bigint not null references users (id) on delete cascade,
    surface            text not null check (surface in ('app', 'extension', 'word', 'outlook', 'document', 'mobile')),
    model              text not null,
    prompt_tokens      integer not null default 0,
    completion_tokens  integer not null default 0,
    estimated          boolean not null default false      -- true when the provider sent no usage numbers
);
create index ai_usage_user_ts_idx on ai_usage (user_id, ts desc);
