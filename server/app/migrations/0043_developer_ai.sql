-- AI in Gær Developer (specs/developer/AI.md). Prompts and answers are never stored (as in app/ai.py); what is kept is
-- which suggestions ("AI foreslår") a user dismissed, so they stay away until the situation changes (a new key).
-- Personal data: in the GDPR export (app/gdpr.py), deleted with the user (cascade), cleaned by retention.
create table dev_ai_dismissals (
    user_id       bigint not null references users (id) on delete cascade,
    key           text not null check (length(key) between 1 and 200),
    kind          text not null check (length(kind) between 1 and 60),
    dismissed_at  timestamptz not null default now(),
    primary key (user_id, key)
);
create index dev_ai_dismissals_at_idx on dev_ai_dismissals (dismissed_at);

-- Every surface that counts AI usage. 0024_tas and 0032_developer_foundation each rebuilt this check without the
-- other's surface (whichever ran last won), so TAS or Developer could not record usage. This is the full list.
alter table ai_usage drop constraint if exists ai_usage_surface_check;
alter table ai_usage add constraint ai_usage_surface_check
    check (surface in ('app', 'extension', 'word', 'outlook', 'document', 'mobile', 'knowledge', 'tas', 'developer'));

insert into retention_policies (key, title, description, days, min_days) values
    ('dev_ai_dismissals', 'Afviste AI-forslag i Developer', 'Hvilke forslag en udvikler har afvist („AI foreslår“)', 365, 30);
