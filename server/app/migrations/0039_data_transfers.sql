-- Data export and import (/admin/data/transfer, app/transfer.py).
--
-- An export is a package (zip with manifest.json and one JSON or CSV file per table, optionally the files' content
-- and optionally encrypted with a password) built by the job data.export and kept in Object Storage for a while.
-- An import is a package uploaded by an admin: a dry run (data.import with dry_run) reports what would happen with the
-- chosen options, the real run applies it, and for UNDO_DAYS the rows it created or changed are kept in
-- data_transfer_changes so the import can be undone.

create table data_transfers (
    id            bigserial primary key,
    kind          text not null check (kind in ('export', 'import')),
    status        text not null check (status in ('queued', 'running', 'ready', 'uploaded', 'dry_running', 'dry_ready',
                                                  'done', 'failed', 'cancelled', 'expired', 'undoing', 'undone')),
    options       jsonb not null default '{}',     -- export: areas, personal, format …; import: the last options run
    password_enc  bytea,                           -- secrets_box, purpose "transfer.password"; cleared when done
    storage_key   text,                            -- the package in Object Storage (null once deleted)
    filename      text not null default '' check (length(filename) <= 200),
    size_bytes    bigint,
    manifest      jsonb,                           -- the package's manifest (import: as read; export: as written)
    report        jsonb,                           -- dry run or result: counts per table, differences, errors
    dry_run_key   text,                            -- hash of the options of the last successful dry run
    job_id        bigint,
    error         text,
    created_by    bigint references users (id) on delete set null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    finished_at   timestamptz,
    expires_at    timestamptz,                     -- export: the download link works until then
    undo_until    timestamptz                      -- import: "Fortryd import" until then
);

create index data_transfers_created on data_transfers (created_at desc);

-- What an import did, so it can be undone: one row per row it inserted (op 'insert', before null) or updated
-- (op 'update', before = the old values of the columns it changed). Deleted after the undo window.
create table data_transfer_changes (
    id           bigserial primary key,
    transfer_id  bigint not null references data_transfers (id) on delete cascade,
    table_name   text not null,
    row_key      jsonb not null,                   -- primary key columns → values
    op           text not null check (op in ('insert', 'update')),
    before       jsonb,
    storage_key  text,                             -- an object the import stored (deleted again on undo)
    created_at   timestamptz not null default now()
);

create index data_transfer_changes_transfer on data_transfer_changes (transfer_id, id);

insert into retention_policies (key, title, description, days, min_days) values
    ('data_transfers', 'Eksport og import', 'Historik over eksporter og importer (pakkerne selv slettes efter 7 dage)', 365, 30);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('data.transfer_cleanup', 'data.transfer_cleanup',
     'Sletter pakker fra eksport og import efter 7 dage og muligheden for at fortryde en import', 3600);

-- Grants are re-applied by gaer_apply_grants() after the run.
