package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/tylerchilds/eve/internal/queue"
)

// JobHandler processes a single job
type JobHandler func(ctx context.Context, job *queue.Job) error

// Engine manages the compute engine: scheduler + worker pools + writer
type Engine struct {
	queue    *queue.Queue
	handlers map[string]JobHandler
	config   Config
}

// Config configures the compute engine
type Config struct {
	WorkerCount     int           // number of concurrent workers
	LeaseTTL        time.Duration // how long to hold a lease
	LeaseOwner      string        // identifier for this engine instance
	BatchSize       int           // how many jobs to lease at once
	PollInterval    time.Duration // how often to poll for new jobs
	RequeueInterval time.Duration // how often to requeue expired leases
	ShutdownTimeout time.Duration // how long to wait for graceful shutdown
}

// DefaultConfig returns sensible defaults
func DefaultConfig() Config {
	return Config{
		WorkerCount: 10,
		LeaseTTL:    5 * time.Minute,
		LeaseOwner:  "engine",
		// Keep workers saturated for bulk backfills by leasing larger batches and polling
		// frequently when idle. Scheduler will also lease immediately when work is available.
		BatchSize:       1000,
		PollInterval:    50 * time.Millisecond,
		RequeueInterval: 30 * time.Second,
		ShutdownTimeout: 30 * time.Second,
	}
}

// New creates a new compute engine
func New(q *queue.Queue, config Config) *Engine {
	return &Engine{
		queue:    q,
		handlers: make(map[string]JobHandler),
		config:   config,
	}
}

// RegisterHandler registers a job handler for a specific job type
func (e *Engine) RegisterHandler(jobType string, handler JobHandler) {
	e.handlers[jobType] = handler
}

// Stats represents engine execution statistics
type Stats struct {
	Succeeded int `json:"succeeded"`
	Failed    int `json:"failed"`
	Skipped   int `json:"skipped"`
}

// Run starts the compute engine and runs until context is cancelled or queue is drained
func (e *Engine) Run(ctx context.Context) (*Stats, error) {
	stats := &Stats{}
	var statsMu sync.Mutex

	schedulerDone := make(chan struct{})

	// Start requeue ticker
	requeueTicker := time.NewTicker(e.config.RequeueInterval)
	defer requeueTicker.Stop()

	// Requeue expired leases immediately on startup
	requeued, err := e.queue.RequeueExpired()
	if err != nil {
		log.Printf("failed to requeue expired leases on startup: %v", err)
	} else if requeued > 0 {
		log.Printf("requeued %d expired leases on startup", requeued)
	}

	// Start requeue goroutine
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-ctx.Done():
				return
			case <-schedulerDone:
				return
			case <-requeueTicker.C:
				requeued, err := e.queue.RequeueExpired()
				if err != nil {
					log.Printf("failed to requeue expired leases: %v", err)
				} else if requeued > 0 {
					log.Printf("requeued %d expired leases", requeued)
				}
			}
		}
	}()

	// Worker pool
	workChan := make(chan *queue.Job, e.config.WorkerCount*2)

	// Start workers
	for i := 0; i < e.config.WorkerCount; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			e.worker(ctx, workerID, workChan, stats, &statsMu)
		}(i)
	}

	// Scheduler loop
	wg.Add(1)
	go func() {
		defer wg.Done()
		defer close(workChan) // Close work channel when scheduler exits
		defer close(schedulerDone)

		for {
			// Respect cancellation
			if ctx.Err() != nil {
				return
			}

			// Lease a batch of jobs
			jobs, err := e.queue.Lease(queue.LeaseOptions{
				LeaseOwner: e.config.LeaseOwner,
				LeaseTTL:   e.config.LeaseTTL,
				BatchSize:  e.config.BatchSize,
			})

			if err != nil {
				// Under very high throughput, the queue DB can briefly return SQLITE_BUSY
				// while other goroutines ACK/FAIL jobs. This is expected with SQLite's
				// single-writer semantics; just retry without spamming logs.
				if !isSQLiteBusy(err) {
					log.Printf("failed to lease jobs: %v", err)
				}
				time.Sleep(e.config.PollInterval)
				continue
			}

			if len(jobs) == 0 {
				// No jobs ready. If queue is fully drained (no pending, no leased), exit.
				qstats, err := e.queue.GetStats()
				if err == nil && qstats.Pending == 0 && qstats.Leased == 0 {
					return
				}
				time.Sleep(e.config.PollInterval)
				continue
			}

			// Send jobs to workers (blocks if workers are saturated).
			for _, job := range jobs {
				select {
				case <-ctx.Done():
					return
				case workChan <- job:
				}
			}
		}
	}()

	// Wait for context cancellation or check for completion
	<-schedulerDone

	// Wait for workers to finish processing current jobs
	shutdownCtx, cancel := context.WithTimeout(context.Background(), e.config.ShutdownTimeout)
	defer cancel()

	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		// Clean shutdown
	case <-shutdownCtx.Done():
		log.Printf("shutdown timeout exceeded, some jobs may not have completed")
	}

	return stats, nil
}

func isSQLiteBusy(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.Contains(s, "database is locked") || strings.Contains(s, "SQLITE_BUSY")
}

// worker processes jobs from the work channel
func (e *Engine) worker(ctx context.Context, workerID int, workChan <-chan *queue.Job, stats *Stats, statsMu *sync.Mutex) {
	for job := range workChan {
		// Check if context is cancelled
		if ctx.Err() != nil {
			return
		}

		// Find handler for job type
		handler, ok := e.handlers[job.Type]
		if !ok {
			log.Printf("worker %d: no handler for job type %s (job %s), failing", workerID, job.Type, job.ID)
			err := e.queue.Fail(queue.FailOptions{
				JobID:    job.ID,
				ErrorMsg: fmt.Sprintf("no handler for job type: %s", job.Type),
			})
			if err != nil {
				log.Printf("worker %d: failed to mark job %s as failed: %v", workerID, job.ID, err)
			}
			statsMu.Lock()
			stats.Skipped++
			statsMu.Unlock()
			continue
		}

		// Execute handler
		err := handler(ctx, job)

		if err != nil {
			log.Printf("worker %d: job %s failed: %v", workerID, job.ID, err)
			failErr := e.queue.Fail(queue.FailOptions{
				JobID:    job.ID,
				ErrorMsg: err.Error(),
			})
			if failErr != nil {
				log.Printf("worker %d: failed to mark job %s as failed: %v", workerID, job.ID, failErr)
			}
			statsMu.Lock()
			stats.Failed++
			statsMu.Unlock()
		} else {
			ackErr := e.queue.Ack(job.ID)
			if ackErr != nil {
				log.Printf("worker %d: failed to ack job %s: %v", workerID, job.ID, ackErr)
			}
			statsMu.Lock()
			stats.Succeeded++
			statsMu.Unlock()
		}
	}
}

// FakeJobPayload is a simple payload for testing
type FakeJobPayload struct {
	Value string `json:"value"`
}

// FakeJobHandler is a simple handler for testing that does nothing
func FakeJobHandler(ctx context.Context, job *queue.Job) error {
	var payload FakeJobPayload
	if err := json.Unmarshal([]byte(job.PayloadJSON), &payload); err != nil {
		return fmt.Errorf("failed to unmarshal payload: %w", err)
	}
	// Do nothing - just succeed
	return nil
}
