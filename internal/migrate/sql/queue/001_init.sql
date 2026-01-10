-- Queue database initialization
-- Creates the durable job queue tables

-- Jobs table: durable job queue with leasing protocol
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'leased', 'succeeded', 'failed', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    run_after_ts INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_ts INTEGER,
    last_error TEXT,
    created_ts INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(type);
CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(lease_owner, lease_expires_ts) WHERE state = 'leased';
CREATE INDEX IF NOT EXISTS idx_jobs_run_after ON jobs(run_after_ts) WHERE state = 'pending';

-- Runs table: tracks compute run sessions (optional; can compute stats from jobs)
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_ts INTEGER NOT NULL,
    config_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_ts);
