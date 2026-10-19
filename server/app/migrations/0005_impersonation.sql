-- Impersonation (from happy-groups admin/impersonation): an admin creates test accounts that cannot
-- log in on their own and views the app on / as one of them. A test account belongs to the admin who
-- created it; nobody else sees or uses it. /admin always runs as the real admin.

alter table users
    add column impersonation_only boolean not null default false,
    add column impersonation_owner_id bigint references users (id) on delete cascade;

alter table users add constraint users_impersonation_owner_check
    check ((impersonation_only and impersonation_owner_id is not null) or (not impersonation_only and impersonation_owner_id is null));

create index users_impersonation_owner_idx on users (impersonation_owner_id) where impersonation_only;

-- An impersonated session belongs to the test account and names the admin behind it. Its token lives in
-- a separate cookie (gaer_imp), next to the admin's own session cookie.
alter table sessions add column impersonator_id bigint references users (id) on delete cascade;
