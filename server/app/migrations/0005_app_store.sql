-- App Stores (/admin/app-stores): one row for the instance's mobile app, after happy-groups
-- member-brand-portal without white-label. Holds the admin's edits only; defaults come from Indstillinger.
--   data.cfg       name, bundleId and colours used for icons, splash and screenshots
--   data.texts     store texts per language (da/en), only the fields that were edited
--   data.links     support, privacy, deletion, contact, review login, ASC/Play app ids …
--   data.manual    manual checklist steps marked as done, per store
--   data.accounts  ids sent by the Happy AI side panel guides (Apple, Google Play, Expo · EAS)

create table app_store (
    id          integer primary key default 1 check (id = 1),
    data        jsonb not null default '{}',
    updated_at  timestamptz not null default now(),
    updated_by  bigint references users (id) on delete set null
);
