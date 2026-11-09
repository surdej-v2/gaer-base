-- ClickHouse (app.clickhouse, /admin/clickhouse): the monitor's check history, retention policies for the data
-- in ClickHouse and the recurring monitor job. The ClickHouse schema itself is in app/clickhouse_migrations.

-- One row per run of the job clickhouse.monitor: overall status and each check. No personal data.
create table clickhouse_checks (
    id          bigserial primary key,
    checked_at  timestamptz not null default now(),
    status      text not null check (status in ('ok', 'warn', 'error')),
    version     text,
    checks      jsonb not null default '[]'
);
create index clickhouse_checks_at_idx on clickhouse_checks (checked_at desc);

-- clickhouse_events and clickhouse_system_logs run against ClickHouse (retention.CLICKHOUSE_CLEANUP);
-- clickhouse_checks is the table above (retention.CLEANUP_SQL).
insert into retention_policies (key, title, description, days, min_days) values
    ('clickhouse_events', 'ClickHouse: hændelser', 'Rækker i ClickHouse-tabellen events', 365, 1),
    ('clickhouse_system_logs', 'ClickHouse: systemlogge', 'system.query_log og system.part_log i ClickHouse', 30, 1),
    ('clickhouse_checks', 'ClickHouse: overvågning', 'Resultater af ClickHouse-tjekket (jobbet clickhouse.monitor)', 30, 1);

insert into job_schedules (name, kind, description, interval_seconds) values
    ('clickhouse.monitor', 'clickhouse.monitor',
     'Tjekker ClickHouse: svar, disk, parts, mutationer og migrationer', 300);
