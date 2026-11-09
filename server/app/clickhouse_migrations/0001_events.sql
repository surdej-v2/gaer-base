-- Events in ClickHouse: an append-only table for counting and graphing what happens in the app.
-- ref is the id of a thing ("clip:12", "job:5"), never of a person: ClickHouse holds no personal data (app.clickhouse).
-- Old rows are deleted by the retention policy clickhouse_events (/admin/jobs).
-- Statements end with ";" at the end of a line; each runs on its own (ClickHouse has no transactions), so every
-- statement in these files must be safe to run twice (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS events (
    ts      DateTime64(3, 'UTC') DEFAULT now64(3),
    kind    LowCardinality(String),
    source  LowCardinality(String) DEFAULT '',
    ref     String DEFAULT '',
    value   Float64 DEFAULT 0,
    data    String DEFAULT '{}' CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (kind, ts);
