-- AI in Gær Developer, fase 4 (specs/developer/AI.md): the API documentation's texts per API, major and section.
-- Sections: 'intro', 'operation:<METHOD> <path>', 'migration' (from the major before), 'errors' (from dev_api_log).
-- Rules write a draft when a version is published (job developer.docs); AI rewrites it when AI is on; people may edit.
-- Only approved_text is published (the OpenAPI document on /gaer/v<major>/<slug>/openapi.json); an owner (A or R)
-- approves. Every approval, edit and rejection is audited (table dev_artifacts, row = the API).
create table dev_api_docs (
    api_artifact_id  bigint not null references dev_artifacts (id) on delete cascade,
    major            integer not null,
    section          text not null check (section ~ '^(intro|migration|errors|operation:[A-Z]+ /.*)$' and length(section) <= 300),
    draft_text       text check (length(draft_text) <= 8000),
    draft_source     text check (draft_source in ('rule', 'ai', 'human')),
    approved_text    text check (length(approved_text) <= 8000),
    approved_by      bigint references users (id) on delete set null,
    approved_at      timestamptz,
    updated_at       timestamptz not null default now(),
    primary key (api_artifact_id, major, section)
);
