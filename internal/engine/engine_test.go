package engine

import (
	"context"
	"database/sql"
	"os"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"

	"github.com/Napageneral/eve/internal/migrate"
	"github.com/Napageneral/eve/internal/queue"
)

func setupTestQueue(t *testing.T) (*sql.DB, *queue.Queue) {
	// Create temp database
	tmpFile, err := os.CreateTemp("", "queue-test-*.db")
	if err != nil {
		t.Fatalf("failed to create temp file: %v", err)
	}
	tmpFile.Close()
	t.Cleanup(func() { os.Remove(tmpFile.Name()) })

	// Run migrations
	if err := migrate.MigrateQueue(tmpFile.Name()); err != nil {
		t.Fatalf("failed to run migrations: %v", err)
	}

	// Open database with WAL mode and busy timeout
	db, err := sql.Open("sqlite3", tmpFile.Name()+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	// Set connection pool limits for better concurrency
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)

	return db, queue.New(db)
}

func TestEngine_Run_EmptyQueue(t *testing.T) {
	_, q := setupTestQueue(t)

	engine := New(q, Config{
		WorkerCount:     2,
		LeaseTTL:        1 * time.Minute,
		LeaseOwner:      "test-engine",
		BatchSize:       10,
		PollInterval:    100 * time.Millisecond,
		RequeueInterval: 1 * time.Second,
		ShutdownTimeout: 5 * time.Second,
	})

	engine.RegisterHandler("fake", FakeJobHandler)

	// Run with a short timeout
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	stats, err := engine.Run(ctx)
	if err != nil {
		t.Fatalf("engine.Run failed: %v", err)
	}

	if stats.Succeeded != 0 {
		t.Errorf("expected 0 succeeded jobs, got %d", stats.Succeeded)
	}
	if stats.Failed != 0 {
		t.Errorf("expected 0 failed jobs, got %d", stats.Failed)
	}
	if stats.Skipped != 0 {
		t.Errorf("expected 0 skipped jobs, got %d", stats.Skipped)
	}
}

func TestEngine_Run_ProcessFakeJobs(t *testing.T) {
	_, q := setupTestQueue(t)

	// Enqueue some fake jobs
	for i := 0; i < 10; i++ {
		err := q.Enqueue(queue.EnqueueOptions{
			Type: "fake",
			Key:  "fake-job-" + string(rune('0'+i)),
			Payload: FakeJobPayload{
				Value: "test-value",
			},
		})
		if err != nil {
			t.Fatalf("failed to enqueue job: %v", err)
		}
	}

	engine := New(q, Config{
		WorkerCount:     3,
		LeaseTTL:        1 * time.Minute,
		LeaseOwner:      "test-engine",
		BatchSize:       5,
		PollInterval:    100 * time.Millisecond,
		RequeueInterval: 1 * time.Second,
		ShutdownTimeout: 5 * time.Second,
	})

	engine.RegisterHandler("fake", FakeJobHandler)

	// Run with timeout
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	stats, err := engine.Run(ctx)
	if err != nil {
		t.Fatalf("engine.Run failed: %v", err)
	}

	if stats.Succeeded != 10 {
		t.Errorf("expected 10 succeeded jobs, got %d", stats.Succeeded)
	}
	if stats.Failed != 0 {
		t.Errorf("expected 0 failed jobs, got %d", stats.Failed)
	}
	if stats.Skipped != 0 {
		t.Errorf("expected 0 skipped jobs, got %d", stats.Skipped)
	}

	// Verify queue is drained
	qstats, err := q.GetStats()
	if err != nil {
		t.Fatalf("failed to get queue stats: %v", err)
	}

	if qstats.Pending != 0 {
		t.Errorf("expected 0 pending jobs, got %d", qstats.Pending)
	}
	if qstats.Succeeded != 10 {
		t.Errorf("expected 10 succeeded jobs in queue, got %d", qstats.Succeeded)
	}
}

func TestEngine_Run_FailedJobs(t *testing.T) {
	_, q := setupTestQueue(t)

	// Enqueue jobs that will fail
	for i := 0; i < 5; i++ {
		err := q.Enqueue(queue.EnqueueOptions{
			Type: "failing",
			Key:  "failing-job-" + string(rune('0'+i)),
			Payload: FakeJobPayload{
				Value: "test-value",
			},
			MaxAttempts: 2, // Fail quickly
		})
		if err != nil {
			t.Fatalf("failed to enqueue job: %v", err)
		}
	}

	engine := New(q, Config{
		WorkerCount:     2,
		LeaseTTL:        1 * time.Minute,
		LeaseOwner:      "test-engine",
		BatchSize:       10,
		PollInterval:    100 * time.Millisecond,
		RequeueInterval: 500 * time.Millisecond,
		ShutdownTimeout: 5 * time.Second,
	})

	// Register handler that always fails
	engine.RegisterHandler("failing", func(ctx context.Context, job *queue.Job) error {
		return context.DeadlineExceeded // simulate failure
	})

	// Run with timeout - give enough time for retries
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stats, err := engine.Run(ctx)
	if err != nil {
		t.Fatalf("engine.Run failed: %v", err)
	}

	// All jobs should have failed after retries
	if stats.Failed < 5 {
		t.Errorf("expected at least 5 failed job attempts, got %d", stats.Failed)
	}

	// Verify jobs are in dead state
	qstats, err := q.GetStats()
	if err != nil {
		t.Fatalf("failed to get queue stats: %v", err)
	}

	if qstats.Dead != 5 {
		t.Errorf("expected 5 dead jobs in queue, got %d", qstats.Dead)
	}
}

func TestEngine_Run_UnknownJobType(t *testing.T) {
	_, q := setupTestQueue(t)

	// Enqueue job with unknown type
	err := q.Enqueue(queue.EnqueueOptions{
		Type: "unknown",
		Key:  "unknown-job",
		Payload: FakeJobPayload{
			Value: "test-value",
		},
		MaxAttempts: 2,
	})
	if err != nil {
		t.Fatalf("failed to enqueue job: %v", err)
	}

	engine := New(q, Config{
		WorkerCount:     1,
		LeaseTTL:        1 * time.Minute,
		LeaseOwner:      "test-engine",
		BatchSize:       10,
		PollInterval:    100 * time.Millisecond,
		RequeueInterval: 1 * time.Second,
		ShutdownTimeout: 5 * time.Second,
	})

	// Don't register any handler for "unknown" type

	// Run with timeout
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	stats, err := engine.Run(ctx)
	if err != nil {
		t.Fatalf("engine.Run failed: %v", err)
	}

	// Depending on timing, the unknown job may be retried within the timeout window,
	// resulting in multiple "skipped" attempts. We only require that it was skipped
	// at least once.
	if stats.Skipped < 1 {
		t.Errorf("expected at least 1 skipped job attempt, got %d", stats.Skipped)
	}
}

func TestEngine_Run_Idempotency(t *testing.T) {
	_, q := setupTestQueue(t)

	// Enqueue same job twice (should dedupe)
	for i := 0; i < 2; i++ {
		err := q.Enqueue(queue.EnqueueOptions{
			Type: "fake",
			Key:  "idempotent-job",
			Payload: FakeJobPayload{
				Value: "test-value",
			},
		})
		if err != nil {
			t.Fatalf("failed to enqueue job: %v", err)
		}
	}

	engine := New(q, Config{
		WorkerCount:     1,
		LeaseTTL:        1 * time.Minute,
		LeaseOwner:      "test-engine",
		BatchSize:       10,
		PollInterval:    100 * time.Millisecond,
		RequeueInterval: 1 * time.Second,
		ShutdownTimeout: 5 * time.Second,
	})

	engine.RegisterHandler("fake", FakeJobHandler)

	// Run with timeout
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	stats, err := engine.Run(ctx)
	if err != nil {
		t.Fatalf("engine.Run failed: %v", err)
	}

	// Should only process 1 job (deduped)
	if stats.Succeeded != 1 {
		t.Errorf("expected 1 succeeded job (deduped), got %d", stats.Succeeded)
	}

	// Verify only 1 job in queue
	qstats, err := q.GetStats()
	if err != nil {
		t.Fatalf("failed to get queue stats: %v", err)
	}

	if qstats.Total != 1 {
		t.Errorf("expected 1 total job in queue (deduped), got %d", qstats.Total)
	}
}
