-- Add job priority for deterministic scheduling of mixed workloads.
-- Higher priority jobs are leased before lower priority jobs.

ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_jobs_pending_priority ON jobs(state, priority, run_after_ts, created_ts) WHERE state = 'pending';

