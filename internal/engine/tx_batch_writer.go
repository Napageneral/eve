package engine

import (
	"context"
	"database/sql"
	"fmt"
	"sync"
	"time"
)

type TxBatchWriterConfig struct {
	BatchSize     int
	FlushInterval time.Duration
}

type txBatchReq struct {
	apply func(tx *sql.Tx) error
	done  chan error
}

// TxBatchWriter serializes DB writes into micro-batched transactions.
// This reduces SQLite write-lock contention under high compute concurrency.
type TxBatchWriter struct {
	db     *sql.DB
	config TxBatchWriterConfig

	ch     chan *txBatchReq
	wg     sync.WaitGroup

	mu       sync.Mutex
	isClosed bool
	closed   chan struct{}
}

func NewTxBatchWriter(db *sql.DB, cfg TxBatchWriterConfig) *TxBatchWriter {
	if cfg.BatchSize <= 0 {
		cfg.BatchSize = 25
	}
	if cfg.FlushInterval <= 0 {
		cfg.FlushInterval = 100 * time.Millisecond
	}
	return &TxBatchWriter{
		db:     db,
		config: cfg,
		ch:     make(chan *txBatchReq, cfg.BatchSize*4),
		closed: make(chan struct{}),
	}
}

func (w *TxBatchWriter) Start() {
	w.wg.Add(1)
	go func() {
		defer w.wg.Done()
		w.run()
	}()
}

func (w *TxBatchWriter) Close() error {
	w.mu.Lock()
	if w.isClosed {
		w.mu.Unlock()
		w.wg.Wait()
		return nil
	}
	w.isClosed = true
	close(w.closed)
	w.mu.Unlock()

	w.wg.Wait()
	return nil
}

func (w *TxBatchWriter) Submit(ctx context.Context, apply func(tx *sql.Tx) error) error {
	if apply == nil {
		return fmt.Errorf("nil apply")
	}
	w.mu.Lock()
	closed := w.isClosed
	w.mu.Unlock()
	if closed {
		return fmt.Errorf("writer closed")
	}

	done := make(chan error, 1)
	req := &txBatchReq{apply: apply, done: done}

	select {
	case <-ctx.Done():
		return ctx.Err()
	case w.ch <- req:
	}

	select {
	case <-ctx.Done():
		return ctx.Err()
	case err := <-done:
		return err
	}
}

func (w *TxBatchWriter) run() {
	ticker := time.NewTicker(w.config.FlushInterval)
	defer ticker.Stop()

	var batch []*txBatchReq

	flush := func() {
		if len(batch) == 0 {
			return
		}
		w.flushBatch(batch)
		batch = batch[:0]
	}

	for {
		select {
		case <-w.closed:
			// Drain any queued requests to avoid deadlocking submitters waiting on done.
			for {
				select {
				case req := <-w.ch:
					if req == nil {
						continue
					}
					batch = append(batch, req)
					if len(batch) >= w.config.BatchSize {
						flush()
					}
				default:
					flush()
					return
				}
			}
		case <-ticker.C:
			flush()
		case req := <-w.ch:
			if req == nil {
				continue
			}
			batch = append(batch, req)
			if len(batch) >= w.config.BatchSize {
				flush()
			}
		}
	}
}

func (w *TxBatchWriter) flushBatch(batch []*txBatchReq) {
	// Try a single tx for the whole batch.
	tx, err := w.db.Begin()
	if err == nil {
		for _, req := range batch {
			if req == nil {
				continue
			}
			if err := req.apply(tx); err != nil {
				_ = tx.Rollback()
				tx = nil
				break
			}
		}
		if tx != nil {
			if err := tx.Commit(); err == nil {
				for _, req := range batch {
					if req == nil {
						continue
					}
					req.done <- nil
				}
				return
			}
			_ = tx.Rollback()
		}
	}

	// Fallback: isolate failures by running each request in its own tx.
	for _, req := range batch {
		if req == nil {
			continue
		}
		req.done <- w.flushOne(req)
	}
}

func (w *TxBatchWriter) flushOne(req *txBatchReq) error {
	tx, err := w.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if err := req.apply(tx); err != nil {
		return err
	}
	return tx.Commit()
}

