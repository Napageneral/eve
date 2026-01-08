package queue

import (
	"database/sql"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// setupTestDB creates a temporary SQLite database with the queue schema
func setupTestDB(t *testing.T) (*sql.DB, func()) {
	t.Helper()

	tmpFile, err := os.CreateTemp("", "queue_test_*.db")
	if err != nil {
		t.Fatalf("failed to create temp db: %v", err)
	}
	tmpFile.Close()

	db, err := sql.Open("sqlite3", tmpFile.Name())
	if err != nil {
		os.Remove(tmpFile.Name())
		t.Fatalf("failed to open db: %v", err)
	}

	// Create schema
	schema := `
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
	`

	if _, err := db.Exec(schema); err != nil {
		db.Close()
		os.Remove(tmpFile.Name())
		t.Fatalf("failed to create schema: %v", err)
	}

	cleanup := func() {
		db.Close()
		os.Remove(tmpFile.Name())
	}

	return db, cleanup
}

func TestEnqueue_Basic(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	err := q.Enqueue(EnqueueOptions{
		Type:    "test_job",
		Key:     "job-1",
		Payload: map[string]string{"foo": "bar"},
	})

	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Verify job was inserted
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM jobs WHERE key = ?", "job-1").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query jobs: %v", err)
	}

	if count != 1 {
		t.Errorf("expected 1 job, got %d", count)
	}
}

func TestEnqueue_Idempotent(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	opts := EnqueueOptions{
		Type:    "test_job",
		Key:     "idempotent-key",
		Payload: map[string]string{"foo": "bar"},
	}

	// Enqueue twice with same key
	err := q.Enqueue(opts)
	if err != nil {
		t.Fatalf("first Enqueue failed: %v", err)
	}

	err = q.Enqueue(opts)
	if err != nil {
		t.Fatalf("second Enqueue failed: %v", err)
	}

	// Should only have one job
	var count int
	err = db.QueryRow("SELECT COUNT(*) FROM jobs WHERE key = ?", "idempotent-key").Scan(&count)
	if err != nil {
		t.Fatalf("failed to query jobs: %v", err)
	}

	if count != 1 {
		t.Errorf("expected 1 job (idempotent), got %d", count)
	}
}

func TestEnqueue_WithDelay(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	now := time.Now().Unix()
	delay := 10 * time.Second

	err := q.Enqueue(EnqueueOptions{
		Type:     "delayed_job",
		Key:      "delayed-1",
		Payload:  map[string]string{"delayed": "true"},
		RunAfter: delay,
	})

	if err != nil {
		t.Fatalf("Enqueue with delay failed: %v", err)
	}

	var runAfterTS int64
	err = db.QueryRow("SELECT run_after_ts FROM jobs WHERE key = ?", "delayed-1").Scan(&runAfterTS)
	if err != nil {
		t.Fatalf("failed to query run_after_ts: %v", err)
	}

	expectedRunAfter := now + int64(delay.Seconds())
	if runAfterTS < expectedRunAfter-1 || runAfterTS > expectedRunAfter+1 {
		t.Errorf("expected run_after_ts around %d, got %d", expectedRunAfter, runAfterTS)
	}
}

func TestLease_Basic(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue some jobs
	for i := 1; i <= 3; i++ {
		err := q.Enqueue(EnqueueOptions{
			Type:    "test_job",
			Key:     string(rune('a' + i)),
			Payload: map[string]int{"num": i},
		})
		if err != nil {
			t.Fatalf("Enqueue failed: %v", err)
		}
	}

	// Lease jobs
	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  10,
	})

	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if len(jobs) != 3 {
		t.Errorf("expected 3 jobs, got %d", len(jobs))
	}

	// Verify all jobs are in leased state
	for _, job := range jobs {
		if job.State != "leased" {
			t.Errorf("expected job %s to be leased, got %s", job.ID, job.State)
		}
		if !job.LeaseOwner.Valid || job.LeaseOwner.String != "worker-1" {
			t.Errorf("expected lease_owner to be worker-1, got %v", job.LeaseOwner)
		}
		if !job.LeaseExpiresTS.Valid {
			t.Errorf("expected lease_expires_ts to be set")
		}
	}
}

func TestLease_BatchSize(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue 10 jobs
	for i := 1; i <= 10; i++ {
		err := q.Enqueue(EnqueueOptions{
			Type:    "test_job",
			Key:     string(rune('a' + i)),
			Payload: map[string]int{"num": i},
		})
		if err != nil {
			t.Fatalf("Enqueue failed: %v", err)
		}
	}

	// Lease with batch size 5
	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  5,
	})

	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if len(jobs) != 5 {
		t.Errorf("expected 5 jobs (batch size), got %d", len(jobs))
	}
}

func TestLease_RespectRunAfter(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue job with delay
	err := q.Enqueue(EnqueueOptions{
		Type:     "delayed_job",
		Key:      "delayed",
		Payload:  map[string]string{"test": "delayed"},
		RunAfter: 10 * time.Second,
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Enqueue immediate job
	err = q.Enqueue(EnqueueOptions{
		Type:    "immediate_job",
		Key:     "immediate",
		Payload: map[string]string{"test": "immediate"},
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Lease should only get the immediate job
	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  10,
	})

	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if len(jobs) != 1 {
		t.Errorf("expected 1 job (only immediate), got %d", len(jobs))
	}

	if len(jobs) > 0 && jobs[0].Key != "immediate" {
		t.Errorf("expected immediate job, got key %s", jobs[0].Key)
	}
}

func TestLease_Empty(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Lease from empty queue
	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  10,
	})

	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if jobs != nil && len(jobs) != 0 {
		t.Errorf("expected empty result, got %d jobs", len(jobs))
	}
}

func TestRequeueExpired_Basic(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue a job
	err := q.Enqueue(EnqueueOptions{
		Type:    "test_job",
		Key:     "job-1",
		Payload: map[string]string{"foo": "bar"},
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Lease with very short TTL
	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   1 * time.Millisecond,
		BatchSize:  10,
	})
	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if len(jobs) != 1 {
		t.Fatalf("expected 1 job, got %d", len(jobs))
	}

	// Wait for lease to expire
	time.Sleep(10 * time.Millisecond)

	// Requeue expired leases
	count, err := q.RequeueExpired()
	if err != nil {
		t.Fatalf("RequeueExpired failed: %v", err)
	}

	if count != 1 {
		t.Errorf("expected 1 requeued job, got %d", count)
	}

	// Verify job is back to pending
	var state string
	err = db.QueryRow("SELECT state FROM jobs WHERE key = ?", "job-1").Scan(&state)
	if err != nil {
		t.Fatalf("failed to query job state: %v", err)
	}

	if state != "pending" {
		t.Errorf("expected state pending, got %s", state)
	}
}

func TestRequeueExpired_NoExpired(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue and lease with long TTL
	err := q.Enqueue(EnqueueOptions{
		Type:    "test_job",
		Key:     "job-1",
		Payload: map[string]string{"foo": "bar"},
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	_, err = q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   1 * time.Hour,
		BatchSize:  10,
	})
	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	// Requeue expired (should find none)
	count, err := q.RequeueExpired()
	if err != nil {
		t.Fatalf("RequeueExpired failed: %v", err)
	}

	if count != 0 {
		t.Errorf("expected 0 requeued jobs, got %d", count)
	}
}

func TestAck_Basic(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue and lease a job
	err := q.Enqueue(EnqueueOptions{
		Type:    "test_job",
		Key:     "job-1",
		Payload: map[string]string{"foo": "bar"},
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  10,
	})
	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if len(jobs) != 1 {
		t.Fatalf("expected 1 job, got %d", len(jobs))
	}

	// Ack the job
	err = q.Ack(jobs[0].ID)
	if err != nil {
		t.Fatalf("Ack failed: %v", err)
	}

	// Verify job is in succeeded state
	var state string
	err = db.QueryRow("SELECT state FROM jobs WHERE id = ?", jobs[0].ID).Scan(&state)
	if err != nil {
		t.Fatalf("failed to query job state: %v", err)
	}

	if state != "succeeded" {
		t.Errorf("expected state succeeded, got %s", state)
	}
}

func TestAck_NotLeased(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue a job but don't lease it
	err := q.Enqueue(EnqueueOptions{
		Type:    "test_job",
		Key:     "job-1",
		Payload: map[string]string{"foo": "bar"},
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	var jobID string
	err = db.QueryRow("SELECT id FROM jobs WHERE key = ?", "job-1").Scan(&jobID)
	if err != nil {
		t.Fatalf("failed to query job id: %v", err)
	}

	// Try to ack without leasing
	err = q.Ack(jobID)
	if err == nil {
		t.Error("expected Ack to fail for non-leased job, but it succeeded")
	}
}

func TestFail_WithRetry(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue and lease a job
	err := q.Enqueue(EnqueueOptions{
		Type:        "test_job",
		Key:         "job-1",
		Payload:     map[string]string{"foo": "bar"},
		MaxAttempts: 3,
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  10,
	})
	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	if len(jobs) != 1 {
		t.Fatalf("expected 1 job, got %d", len(jobs))
	}

	// Fail the job (should retry)
	err = q.Fail(FailOptions{
		JobID:    jobs[0].ID,
		ErrorMsg: "test error",
	})
	if err != nil {
		t.Fatalf("Fail failed: %v", err)
	}

	// Verify job is back to pending with incremented attempts
	var state string
	var attempts int
	var lastError sql.NullString
	err = db.QueryRow(
		"SELECT state, attempts, last_error FROM jobs WHERE id = ?",
		jobs[0].ID,
	).Scan(&state, &attempts, &lastError)
	if err != nil {
		t.Fatalf("failed to query job: %v", err)
	}

	if state != "pending" {
		t.Errorf("expected state pending, got %s", state)
	}
	if attempts != 1 {
		t.Errorf("expected attempts 1, got %d", attempts)
	}
	if !lastError.Valid || lastError.String != "test error" {
		t.Errorf("expected last_error 'test error', got %v", lastError)
	}
}

func TestFail_MaxAttempts(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue a job with max_attempts = 1
	err := q.Enqueue(EnqueueOptions{
		Type:        "test_job",
		Key:         "job-1",
		Payload:     map[string]string{"foo": "bar"},
		MaxAttempts: 1,
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	jobs, err := q.Lease(LeaseOptions{
		LeaseOwner: "worker-1",
		LeaseTTL:   30 * time.Second,
		BatchSize:  10,
	})
	if err != nil {
		t.Fatalf("Lease failed: %v", err)
	}

	// Fail the job (should go to dead state)
	err = q.Fail(FailOptions{
		JobID:    jobs[0].ID,
		ErrorMsg: "fatal error",
	})
	if err != nil {
		t.Fatalf("Fail failed: %v", err)
	}

	// Verify job is in dead state
	var state string
	var attempts int
	err = db.QueryRow(
		"SELECT state, attempts FROM jobs WHERE id = ?",
		jobs[0].ID,
	).Scan(&state, &attempts)
	if err != nil {
		t.Fatalf("failed to query job: %v", err)
	}

	if state != "dead" {
		t.Errorf("expected state dead, got %s", state)
	}
	if attempts != 1 {
		t.Errorf("expected attempts 1, got %d", attempts)
	}
}

func TestFail_ExponentialBackoff(t *testing.T) {
	db, cleanup := setupTestDB(t)
	defer cleanup()

	q := New(db)

	// Enqueue a job
	err := q.Enqueue(EnqueueOptions{
		Type:        "test_job",
		Key:         "job-1",
		Payload:     map[string]string{"foo": "bar"},
		MaxAttempts: 5,
	})
	if err != nil {
		t.Fatalf("Enqueue failed: %v", err)
	}

	// Lease and fail multiple times, checking backoff
	for expectedAttempts := 1; expectedAttempts <= 3; expectedAttempts++ {
		jobs, err := q.Lease(LeaseOptions{
			LeaseOwner: "worker-1",
			LeaseTTL:   30 * time.Second,
			BatchSize:  10,
		})
		if err != nil {
			t.Fatalf("Lease failed on attempt %d: %v", expectedAttempts, err)
		}

		if len(jobs) != 1 {
			t.Fatalf("expected 1 job on attempt %d, got %d", expectedAttempts, len(jobs))
		}

		now := time.Now().Unix()

		err = q.Fail(FailOptions{
			JobID:    jobs[0].ID,
			ErrorMsg: "retry test",
		})
		if err != nil {
			t.Fatalf("Fail failed on attempt %d: %v", expectedAttempts, err)
		}

		// Check backoff: should be 2^attempts seconds
		var runAfterTS int64
		err = db.QueryRow(
			"SELECT run_after_ts FROM jobs WHERE id = ?",
			jobs[0].ID,
		).Scan(&runAfterTS)
		if err != nil {
			t.Fatalf("failed to query run_after_ts: %v", err)
		}

		expectedBackoff := int64(1 << uint(expectedAttempts))
		expectedRunAfter := now + expectedBackoff

		// Allow 1 second tolerance
		if runAfterTS < expectedRunAfter-1 || runAfterTS > expectedRunAfter+1 {
			t.Errorf("attempt %d: expected run_after around %d (backoff %ds), got %d",
				expectedAttempts, expectedRunAfter, expectedBackoff, runAfterTS)
		}

		// Manually reset to immediate for next iteration
		_, err = db.Exec("UPDATE jobs SET run_after_ts = ? WHERE id = ?", now, jobs[0].ID)
		if err != nil {
			t.Fatalf("failed to reset run_after_ts: %v", err)
		}
	}
}
