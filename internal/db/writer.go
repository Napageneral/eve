package db

import (
	"database/sql"
	"fmt"
	"sync"
	"time"
)

// Writer manages batched writes to the warehouse DB (eve.db)
type Writer struct {
	db      *sql.DB
	mu      sync.Mutex
	batch   []WriteOp
	config  WriterConfig
	flushCh chan struct{}
	stopCh  chan struct{}
	wg      sync.WaitGroup
}

// WriterConfig configures the database writer
type WriterConfig struct {
	BatchSize     int           // max operations per batch
	FlushInterval time.Duration // max time before flushing
}

// DefaultWriterConfig returns sensible defaults
func DefaultWriterConfig() WriterConfig {
	return WriterConfig{
		BatchSize:     100,
		FlushInterval: 1 * time.Second,
	}
}

// WriteOp represents a database write operation
type WriteOp struct {
	Query string
	Args  []interface{}
}

// NewWriter creates a new database writer
func NewWriter(db *sql.DB, config WriterConfig) *Writer {
	w := &Writer{
		db:      db,
		batch:   make([]WriteOp, 0, config.BatchSize),
		config:  config,
		flushCh: make(chan struct{}, 1),
		stopCh:  make(chan struct{}),
	}

	// Start flush timer
	w.wg.Add(1)
	go w.flushLoop()

	return w
}

// Write adds a write operation to the batch
func (w *Writer) Write(query string, args ...interface{}) error {
	w.mu.Lock()
	defer w.mu.Unlock()

	w.batch = append(w.batch, WriteOp{
		Query: query,
		Args:  args,
	})

	// Trigger flush if batch is full
	if len(w.batch) >= w.config.BatchSize {
		select {
		case w.flushCh <- struct{}{}:
		default:
		}
	}

	return nil
}

// Flush immediately writes all pending operations
func (w *Writer) Flush() error {
	w.mu.Lock()
	if len(w.batch) == 0 {
		w.mu.Unlock()
		return nil
	}

	ops := w.batch
	w.batch = make([]WriteOp, 0, w.config.BatchSize)
	w.mu.Unlock()

	return w.executeBatch(ops)
}

// flushLoop periodically flushes the batch
func (w *Writer) flushLoop() {
	defer w.wg.Done()

	ticker := time.NewTicker(w.config.FlushInterval)
	defer ticker.Stop()

	for {
		select {
		case <-w.stopCh:
			// Final flush before exit
			w.Flush()
			return
		case <-ticker.C:
			w.Flush()
		case <-w.flushCh:
			w.Flush()
		}
	}
}

// executeBatch executes all operations in a single transaction
func (w *Writer) executeBatch(ops []WriteOp) error {
	if len(ops) == 0 {
		return nil
	}

	tx, err := w.db.Begin()
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer tx.Rollback()

	for i, op := range ops {
		_, err := tx.Exec(op.Query, op.Args...)
		if err != nil {
			return fmt.Errorf("failed to execute operation %d: %w", i, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}

// Close stops the writer and flushes pending operations
func (w *Writer) Close() error {
	close(w.stopCh)
	w.wg.Wait()
	return nil
}
