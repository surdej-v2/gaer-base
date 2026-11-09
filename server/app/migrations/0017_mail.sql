-- Mail, calendar and contacts (specs/mail). Stalwart is the system of record for mail, events, contacts and
-- credentials; these tables only map app users to Stalwart principals and hold what the pages manage.
-- app/mailbox/ holds the rules, routes/mail.py (/api/mail) and routes/admin_mail.py (/api/admin/mail) the API.

create table mail_domains (
    id                   bigserial primary key,
    name                 text not null unique check (name = lower(name) and name ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'),
    status               text not null default 'pending' check (status in ('pending', 'verified', 'suspended')),
    dkim_selector        text not null default 'lm' check (dkim_selector ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    lettermint_domain_id text,
    checks               jsonb not null default '{}',   -- the last live DNS check, per record
    checked_at           timestamptz,
    created_by           bigint references users (id) on delete set null,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

-- One personal mailbox per user (owner_id, the spec's mailbox_owner) and any number of shared ones. A shared
-- mailbox is a Stalwart group principal; its members and their roles are in mail_shared_members.
create table mailboxes (
    id                  bigserial primary key,
    domain_id           bigint not null references mail_domains (id),
    local_part          text not null check (local_part ~ '^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9])?$'),
    kind                text not null check (kind in ('personal', 'shared')),
    owner_id            bigint references users (id) on delete cascade,
    display_name        text,
    stalwart_principal  text not null,
    quota_mb            integer not null default 5120 check (quota_mb between 10 and 1048576),
    status              text not null default 'provisioning'
                            check (status in ('provisioning', 'active', 'suspended', 'deleted')),
    provision_error     text,
    used_bytes          bigint,
    last_login_at       timestamptz,
    requested_at        timestamptz not null default now(),
    provisioned_at      timestamptz,
    synced_at           timestamptz,
    drift               text,                           -- set by the nightly sync when Stalwart and the app disagree
    created_by          bigint references users (id) on delete set null,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    check ((kind = 'personal') = (owner_id is not null))
);
create unique index mailboxes_address_idx on mailboxes (domain_id, local_part) where status <> 'deleted';
create unique index mailboxes_principal_idx on mailboxes (stalwart_principal) where status <> 'deleted';
create unique index mailboxes_owner_idx on mailboxes (owner_id) where kind = 'personal' and status <> 'deleted';

create table mail_aliases (
    id          bigserial primary key,
    mailbox_id  bigint not null references mailboxes (id) on delete cascade,
    address     text not null unique check (address = lower(address) and address like '%_@_%'),
    created_by  bigint references users (id) on delete set null,
    created_at  timestamptz not null default now()
);

-- A user asks for an alias on /user/mail; an admin approves or declines it.
create table mail_alias_requests (
    id          bigserial primary key,
    user_id     bigint not null references users (id) on delete cascade,
    mailbox_id  bigint not null references mailboxes (id) on delete cascade,
    address     text not null check (address = lower(address) and address like '%_@_%'),
    note        text not null default '',
    status      text not null default 'pending' check (status in ('pending', 'approved', 'declined')),
    decided_by  bigint references users (id) on delete set null,
    decided_at  timestamptz,
    created_at  timestamptz not null default now()
);
create unique index mail_alias_requests_pending_idx on mail_alias_requests (address) where status = 'pending';

create table mail_shared_members (
    mailbox_id  bigint not null references mailboxes (id) on delete cascade,
    user_id     bigint not null references users (id) on delete cascade,
    role        text not null check (role in ('reader', 'sender', 'manager')),
    added_by    bigint references users (id) on delete set null,
    added_at    timestamptz not null default now(),
    primary key (mailbox_id, user_id)
);
create index mail_shared_members_user_idx on mail_shared_members (user_id);

-- From addresses a user may send as: the personal address, its aliases, and shared addresses where the user is
-- sender or manager. One row per user and address, with that user's display name and signature.
create table mail_identities (
    id            bigserial primary key,
    user_id       bigint not null references users (id) on delete cascade,
    mailbox_id    bigint not null references mailboxes (id) on delete cascade,
    from_address  text not null check (from_address = lower(from_address)),
    display_name  text not null default '',
    signature     text not null default '',
    updated_at    timestamptz not null default now(),
    unique (user_id, from_address)
);

-- Calendars and address books live in Stalwart; the app keeps the id and name for the pages. A personal one belongs
-- to the user's principal, a shared one to its own Stalwart group principal.
create table mail_collections (
    id               uuid primary key default gen_random_uuid(),
    type             text not null check (type in ('calendar', 'address_book')),
    kind             text not null check (kind in ('personal', 'shared')),
    owner_id         bigint references users (id) on delete cascade,
    owner_principal  text not null,
    slug             text not null check (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name             text not null check (length(name) between 1 and 120),
    colour           text check (colour ~ '^#[0-9a-fA-F]{6}$'),
    timezone         text,
    is_default       boolean not null default false,
    freebusy         boolean not null default true,     -- calendars: counts in published free/busy
    entry_count      integer,
    created_by       bigint references users (id) on delete set null,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    check ((kind = 'personal') = (owner_id is not null)),
    unique (owner_principal, type, slug)
);
create unique index mail_collections_default_idx on mail_collections (owner_id, type) where is_default;

create table mail_collection_shares (
    collection_id  uuid not null references mail_collections (id) on delete cascade,
    user_id        bigint not null references users (id) on delete cascade,
    role           text not null check (role in ('viewer', 'editor', 'manager')),
    added_by       bigint references users (id) on delete set null,
    added_at       timestamptz not null default now(),
    primary key (collection_id, user_id)
);
create index mail_collection_shares_user_idx on mail_collection_shares (user_id);

-- Working hours and free/busy publishing per user (pushed to Stalwart's scheduling settings).
create table mail_availability (
    user_id        bigint primary key references users (id) on delete cascade,
    publish        boolean not null default true,
    working_hours  jsonb not null default '{"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "16:00"}',
    timezone       text not null default 'Europe/Copenhagen',
    updated_at     timestamptz not null default now()
);

-- App passwords for IMAP/SMTP/DAV clients. The secret exists only in Stalwart and is shown once.
create table mail_app_passwords (
    id            bigserial primary key,
    user_id       bigint not null references users (id) on delete cascade,
    mailbox_id    bigint not null references mailboxes (id) on delete cascade,
    name          text not null check (length(name) between 1 and 60),
    secret_name   text not null unique,                 -- the name Stalwart knows it by
    created_at    timestamptz not null default now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    revoked_by    bigint references users (id) on delete set null
);
create index mail_app_passwords_user_idx on mail_app_passwords (user_id) where revoked_at is null;

-- Mail expiry: global, per domain or per mailbox; at most 30 days. Calendars and contacts never expire.
create table mail_retention_policies (
    id          bigserial primary key,
    scope       text not null check (scope in ('global', 'domain', 'mailbox')),
    domain_id   bigint references mail_domains (id) on delete cascade,
    mailbox_id  bigint references mailboxes (id) on delete cascade,
    days        integer not null check (days between 1 and 30),
    applies_to  text not null default 'all' check (applies_to in ('all', 'trash', 'junk')),
    updated_by  bigint references users (id) on delete set null,
    updated_at  timestamptz not null default now(),
    check ((scope = 'global' and domain_id is null and mailbox_id is null)
        or (scope = 'domain' and domain_id is not null and mailbox_id is null)
        or (scope = 'mailbox' and mailbox_id is not null and domain_id is null))
);
create unique index mail_retention_scope_idx on mail_retention_policies
    (scope, coalesce(domain_id, 0), coalesce(mailbox_id, 0));
insert into mail_retention_policies (scope, days, applies_to) values ('global', 30, 'all');

create table mail_retention_runs (
    id            bigserial primary key,
    dry_run       boolean not null default false,
    triggered_by  bigint references users (id) on delete set null,
    status        text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
    mailboxes     integer not null default 0,
    expired       integer not null default 0,
    archived      integer not null default 0,
    failures      integer not null default 0,
    detail        jsonb not null default '{}',
    started_at    timestamptz not null default now(),
    finished_at   timestamptz
);

-- Delivery events from Lettermint (incoming webhook handler "lettermint") and expiry counts from the retention job.
-- No message content and no recipient address: only the recipient's domain.
create table mail_events (
    id                bigserial primary key,
    mailbox_id        bigint references mailboxes (id) on delete cascade,
    type              text not null check (type in ('sent', 'delivered', 'bounced', 'complained', 'expired')),
    message_id        text,
    from_address      text,
    recipient_domain  text,
    count             integer not null default 1,
    provider_event_id text unique,
    detail            jsonb not null default '{}',
    at                timestamptz not null default now()
);
create index mail_events_mailbox_idx on mail_events (mailbox_id, at desc);
create index mail_events_at_idx on mail_events (at desc, type);

insert into retention_policies (key, title, description, days, min_days) values
    ('mail_events', 'Mailhændelser', 'Leverings-, bounce- og klagehændelser fra Lettermint og udløbstællinger', 180, 30),
    ('mail_retention_runs', 'Kørsler af mailoprydning', 'Historik over kørsler af 30-dages-sletningen af mail', 365, 30),
    ('mail_alias_requests', 'Besvarede aliasanmodninger', 'Godkendte og afviste anmodninger om alias', 90, 7);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('mail.retention', 'mail.retention', 'Sletter mail ældre end opbevaringsreglen (højst 30 dage) gennem Stalwart', 86400),
    ('mail.sync', 'mail.sync', 'Sammenligner postkasser i appen og i Stalwart og melder afvigelser', 86400),
    ('mail.backup', 'mail.backup', 'Eksporterer kalendere (.ics) og adressebøger (.vcf) til backup', 86400);

-- Grants are re-applied by gaer_apply_grants() after the run.
