-- Tracks known buckets and their associated credential profile
CREATE TABLE IF NOT EXISTS buckets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    region      TEXT,
    profile     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, profile)
);

-- Daily storage snapshots from bucket scans
CREATE TABLE IF NOT EXISTS bucket_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_id       INTEGER NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    snapshot_date   TEXT NOT NULL,
    total_objects   INTEGER,
    total_bytes     INTEGER,
    standard_bytes  INTEGER DEFAULT 0,
    ia_bytes        INTEGER DEFAULT 0,
    glacier_bytes   INTEGER DEFAULT 0,
    deep_archive_bytes  INTEGER DEFAULT 0,
    intelligent_tiering_bytes INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(bucket_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_bucket_date ON bucket_snapshots(bucket_id, snapshot_date);

-- Daily API usage and transfer volume
CREATE TABLE IF NOT EXISTS daily_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_id       INTEGER NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    usage_date      TEXT NOT NULL,
    bytes_uploaded  INTEGER DEFAULT 0,
    bytes_downloaded INTEGER DEFAULT 0,
    put_requests    INTEGER DEFAULT 0,
    get_requests    INTEGER DEFAULT 0,
    list_requests   INTEGER DEFAULT 0,
    delete_requests INTEGER DEFAULT 0,
    copy_requests   INTEGER DEFAULT 0,
    head_requests   INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(bucket_id, usage_date)
);
CREATE INDEX IF NOT EXISTS idx_usage_bucket_date ON daily_usage(bucket_id, usage_date);

-- Configurable cost rates
CREATE TABLE IF NOT EXISTS cost_rates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    rate        REAL NOT NULL,
    unit        TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed default cost rates (US East pricing as of 2025)
INSERT OR IGNORE INTO cost_rates (name, rate, unit) VALUES
    ('storage_standard_gb_month', 0.023, '$/GB/month'),
    ('storage_ia_gb_month', 0.0125, '$/GB/month'),
    ('storage_glacier_gb_month', 0.004, '$/GB/month'),
    ('storage_deep_archive_gb_month', 0.00099, '$/GB/month'),
    ('storage_intelligent_tiering_gb_month', 0.023, '$/GB/month'),
    ('put_request', 0.000005, '$/request'),
    ('get_request', 0.0000004, '$/request'),
    ('list_request', 0.000005, '$/request'),
    ('delete_request', 0.0, '$/request'),
    ('copy_request', 0.000005, '$/request'),
    ('head_request', 0.0000004, '$/request'),
    ('transfer_out_gb_first_100', 0.09, '$/GB'),
    ('transfer_out_gb_next_10k', 0.085, '$/GB'),
    ('transfer_in_gb', 0.0, '$/GB');

-- Transfer queue with resume support
CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_id       INTEGER NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    object_key      TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('upload', 'download')),
    total_bytes     INTEGER,
    transferred     INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','in_progress','paused','completed','failed','cancelled')),
    upload_id       TEXT,
    local_path      TEXT NOT NULL,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);

-- Individual parts for multipart uploads (resume support)
CREATE TABLE IF NOT EXISTS transfer_parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id     INTEGER NOT NULL REFERENCES transfers(id) ON DELETE CASCADE,
    part_number     INTEGER NOT NULL,
    offset          INTEGER NOT NULL,
    size            INTEGER NOT NULL,
    etag            TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','in_progress','completed','failed')),
    UNIQUE(transfer_id, part_number)
);

-- Key-value store for app preferences and UI state
CREATE TABLE IF NOT EXISTS preferences (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- Schema version tracking for migrations
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
