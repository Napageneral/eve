package queue

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
)

// Job represents a durable queue job
type Job struct {
	ID             string
	Type           string
	Key            string
	PayloadJSON    string
	State          string
	Attempts       int
	MaxAttempts    int
	RunAfterTS     int64
	LeaseOwner     sql.NullString
	LeaseExpiresTS sql.NullInt64
	LastError      sql.NullString
	CreatedTS      int64
	UpdatedTS      int64
}

// Queue manages durable job queue operations
type Queue struct {
	db *sql.DB
}

// New creates a new Queue instance
func New(db *sql.DB) *Queue {
	return &Queue{db: db}
}

// EnqueueOptions configures job enqueueing
type EnqueueOptions struct {
	Type        string
	Key         string
	Payload     interface{}
	MaxAttempts int
	RunAfter    time.Duration // delay before job becomes available
}

// Enqueue adds a job to the queue idempotently using the unique key
func (q *Queue) Enqueue(opts EnqueueOptions) error {
	// Serialize payload to JSON
	payloadJSON, err := json.Marshal(opts.Payload)
	if err != nil {
		return fmt.Errorf("failed to marshal payload: %w", err)
	}

	maxAttempts := opts.MaxAttempts
	if maxAttempts == 0 {
		maxAttempts = 8 // default from schema
	}

	now := time.Now().Unix()
	runAfterTS := now
	if opts.RunAfter > 0 {
		runAfterTS = now + int64(opts.RunAfter.Seconds())
	}

	// Idempotent insert using UNIQUE constraint on key
	// If key exists, update nothing (no-op)
	_, err = q.db.Exec(`
		INSERT INTO jobs (
			id, type, key, payload_json, state, attempts, max_attempts,
			run_after_ts, created_ts, updated_ts
		) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
		ON CONFLICT(key) DO NOTHING
	`, uuid.New().String(), opts.Type, opts.Key, string(payloadJSON),
		maxAttempts, runAfterTS, now, now)

	if err != nil {
		return fmt.Errorf("failed to enqueue job: %w", err)
	}

	return nil
}

// LeaseOptions configures job leasing
type LeaseOptions struct {
	LeaseOwner string
	LeaseTTL   time.Duration
	BatchSize  int
}

// Lease atomically claims pending jobs and returns them
func (q *Queue) Lease(opts LeaseOptions) ([]*Job, error) {
	if opts.BatchSize == 0 {
		opts.BatchSize = 10
	}

	now := time.Now().Unix()
	leaseExpiresTS := now + int64(opts.LeaseTTL.Seconds())

	// Begin transaction to atomically lease jobs
	tx, err := q.db.Begin()
	if err != nil {
		return nil, fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Find pending jobs that are ready to run
	rows, err := tx.Query(`
		SELECT id, type, key, payload_json, state, attempts, max_attempts,
		       run_after_ts, lease_owner, lease_expires_ts, last_error,
		       created_ts, updated_ts
		FROM jobs
		WHERE state = 'pending'
		  AND run_after_ts <= ?
		ORDER BY run_after_ts, created_ts
		LIMIT ?
	`, now, opts.BatchSize)
	if err != nil {
		return nil, fmt.Errorf("failed to query pending jobs: %w", err)
	}

	var jobs []*Job
	var jobIDs []string

	for rows.Next() {
		job := &Job{}
		err := rows.Scan(
			&job.ID, &job.Type, &job.Key, &job.PayloadJSON, &job.State,
			&job.Attempts, &job.MaxAttempts, &job.RunAfterTS,
			&job.LeaseOwner, &job.LeaseExpiresTS, &job.LastError,
			&job.CreatedTS, &job.UpdatedTS,
		)
		if err != nil {
			rows.Close()
			return nil, fmt.Errorf("failed to scan job: %w", err)
		}
		jobs = append(jobs, job)
		jobIDs = append(jobIDs, job.ID)
	}
	rows.Close()

	if err = rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating jobs: %w", err)
	}

	// No jobs to lease
	if len(jobs) == 0 {
		return nil, nil
	}

	// Update jobs to leased state
	// Build IN clause for job IDs
	query := `
		UPDATE jobs
		SET state = 'leased',
		    lease_owner = ?,
		    lease_expires_ts = ?,
		    updated_ts = ?
		WHERE id IN (`

	args := []interface{}{opts.LeaseOwner, leaseExpiresTS, now}
	for i, id := range jobIDs {
		if i > 0 {
			query += ", "
		}
		query += "?"
		args = append(args, id)
	}
	query += ")"

	_, err = tx.Exec(query, args...)
	if err != nil {
		return nil, fmt.Errorf("failed to update leased jobs: %w", err)
	}

	if err = tx.Commit(); err != nil {
		return nil, fmt.Errorf("failed to commit lease transaction: %w", err)
	}

	// Update jobs with new state for return
	for _, job := range jobs {
		job.State = "leased"
		job.LeaseOwner = sql.NullString{String: opts.LeaseOwner, Valid: true}
		job.LeaseExpiresTS = sql.NullInt64{Int64: leaseExpiresTS, Valid: true}
		job.UpdatedTS = now
	}

	return jobs, nil
}

// RequeueExpired moves expired leases back to pending state
func (q *Queue) RequeueExpired() (int, error) {
	now := time.Now().Unix()

	result, err := q.db.Exec(`
		UPDATE jobs
		SET state = 'pending',
		    lease_owner = NULL,
		    lease_expires_ts = NULL,
		    updated_ts = ?
		WHERE state = 'leased'
		  AND lease_expires_ts <= ?
	`, now, now)

	if err != nil {
		return 0, fmt.Errorf("failed to requeue expired leases: %w", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return 0, fmt.Errorf("failed to get rows affected: %w", err)
	}

	return int(rowsAffected), nil
}

// Ack marks a job as successfully completed
func (q *Queue) Ack(jobID string) error {
	now := time.Now().Unix()

	result, err := q.db.Exec(`
		UPDATE jobs
		SET state = 'succeeded',
		    lease_owner = NULL,
		    lease_expires_ts = NULL,
		    updated_ts = ?
		WHERE id = ?
		  AND state = 'leased'
	`, now, jobID)

	if err != nil {
		return fmt.Errorf("failed to ack job: %w", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}

	if rowsAffected == 0 {
		return fmt.Errorf("job %s not found or not in leased state", jobID)
	}

	return nil
}

// FailOptions configures job failure handling
type FailOptions struct {
	JobID      string
	ErrorMsg   string
	RetryDelay time.Duration
}

// Fail marks a job as failed and either retries or moves to dead state
func (q *Queue) Fail(opts FailOptions) error {
	now := time.Now().Unix()

	// Begin transaction to read and update atomically
	tx, err := q.db.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	// Read current job state
	var attempts, maxAttempts int
	err = tx.QueryRow(`
		SELECT attempts, max_attempts
		FROM jobs
		WHERE id = ? AND state = 'leased'
	`, opts.JobID).Scan(&attempts, &maxAttempts)

	if err == sql.ErrNoRows {
		return fmt.Errorf("job %s not found or not in leased state", opts.JobID)
	}
	if err != nil {
		return fmt.Errorf("failed to read job: %w", err)
	}

	attempts++

	// Determine next state and run_after
	var nextState string
	var runAfterTS int64

	if attempts >= maxAttempts {
		nextState = "dead"
		runAfterTS = now
	} else {
		nextState = "pending"
		// Exponential backoff: 2^attempts seconds
		backoffSeconds := int64(1 << uint(attempts))
		if opts.RetryDelay > 0 {
			backoffSeconds = int64(opts.RetryDelay.Seconds())
		}
		runAfterTS = now + backoffSeconds
	}

	// Update job
	_, err = tx.Exec(`
		UPDATE jobs
		SET state = ?,
		    attempts = ?,
		    run_after_ts = ?,
		    lease_owner = NULL,
		    lease_expires_ts = NULL,
		    last_error = ?,
		    updated_ts = ?
		WHERE id = ?
	`, nextState, attempts, runAfterTS, opts.ErrorMsg, now, opts.JobID)

	if err != nil {
		return fmt.Errorf("failed to update failed job: %w", err)
	}

	if err = tx.Commit(); err != nil {
		return fmt.Errorf("failed to commit fail transaction: %w", err)
	}

	return nil
}
