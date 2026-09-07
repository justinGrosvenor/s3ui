-- Persist the source identity so resumed transfers cannot combine different files.
ALTER TABLE transfers ADD COLUMN source_etag TEXT;
ALTER TABLE transfers ADD COLUMN source_mtime_ns INTEGER;
CREATE INDEX idx_transfers_bucket_queue ON transfers(bucket_id, status, created_at, id);
CREATE INDEX idx_transfers_download_path ON transfers(local_path, direction, status);
