-- Warehouse database watermarks
-- Tracks ETL sync progress for incremental updates

CREATE TABLE IF NOT EXISTS watermarks (
    source TEXT NOT NULL,
    name TEXT NOT NULL,
    value_int INTEGER,
    value_text TEXT,
    updated_ts INTEGER NOT NULL,
    PRIMARY KEY (source, name)
);

CREATE INDEX IF NOT EXISTS idx_watermarks_updated ON watermarks(updated_ts);
