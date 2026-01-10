package embeddings

import (
	"context"
	"sync"
	"time"

	"github.com/brandtty/eve/internal/gemini"
)

const (
	defaultMaxBatchSize  = 100 // Max texts per batch
	defaultFlushInterval = 1 * time.Second
)

// EmbeddingTask represents a single embedding task
type EmbeddingTask struct {
	EntityType string
	EntityID   int
	Text       string
}

// EmbeddingResult represents the result of an embedding task
type EmbeddingResult struct {
	Task      EmbeddingTask
	Embedding []float64
	Error     error
}

// Batcher batches embedding tasks for efficient API calls
type Batcher struct {
	client        *gemini.Client
	model         string
	maxBatchSize  int
	flushInterval time.Duration

	mu      sync.Mutex
	batch   []EmbeddingTask
	results chan EmbeddingResult

	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup
}

// NewBatcher creates a new embedding batcher
func NewBatcher(client *gemini.Client, model string) *Batcher {
	ctx, cancel := context.WithCancel(context.Background())
	b := &Batcher{
		client:        client,
		model:         model,
		maxBatchSize:  defaultMaxBatchSize,
		flushInterval: defaultFlushInterval,
		batch:         make([]EmbeddingTask, 0, defaultMaxBatchSize),
		results:       make(chan EmbeddingResult, 100),
		ctx:           ctx,
		cancel:        cancel,
	}

	// Start flush timer goroutine
	b.wg.Add(1)
	go b.timerLoop()

	return b
}

// Add adds a task to the batch
func (b *Batcher) Add(task EmbeddingTask) {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.batch = append(b.batch, task)

	// Flush if batch is full
	if len(b.batch) >= b.maxBatchSize {
		b.flushLocked()
	}
}

// Results returns the results channel
func (b *Batcher) Results() <-chan EmbeddingResult {
	return b.results
}

// Flush flushes any pending tasks in the batch
func (b *Batcher) Flush() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.flushLocked()
}

// flushLocked flushes the batch (must be called with lock held)
func (b *Batcher) flushLocked() {
	if len(b.batch) == 0 {
		return
	}

	// Copy batch for processing
	tasks := make([]EmbeddingTask, len(b.batch))
	copy(tasks, b.batch)
	b.batch = b.batch[:0] // Clear batch

	// Process batch in background
	b.wg.Add(1)
	go func() {
		defer b.wg.Done()
		b.processBatch(tasks)
	}()
}

// processBatch processes a batch of embedding tasks
func (b *Batcher) processBatch(tasks []EmbeddingTask) {
	if len(tasks) == 0 {
		return
	}

	// Build batch request
	requests := make([]gemini.EmbedContentRequest, len(tasks))
	for i, task := range tasks {
		requests[i] = gemini.EmbedContentRequest{
			Model: b.model,
			Content: gemini.Content{
				Parts: []gemini.Part{
					{Text: task.Text},
				},
			},
		}
	}

	// Call Gemini batch API
	resp, err := b.client.BatchEmbedContents(b.model, requests)
	if err != nil {
		// Send error for all tasks in batch
		for _, task := range tasks {
			select {
			case b.results <- EmbeddingResult{Task: task, Error: err}:
			case <-b.ctx.Done():
				return
			}
		}
		return
	}

	// Send results
	for i, task := range tasks {
		var embedding []float64
		var taskErr error

		if i < len(resp.Embeddings) {
			embedding = resp.Embeddings[i].Values
		} else {
			taskErr = err
		}

		select {
		case b.results <- EmbeddingResult{Task: task, Embedding: embedding, Error: taskErr}:
		case <-b.ctx.Done():
			return
		}
	}
}

// timerLoop periodically flushes the batch
func (b *Batcher) timerLoop() {
	defer b.wg.Done()

	ticker := time.NewTicker(b.flushInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			b.Flush()
		case <-b.ctx.Done():
			return
		}
	}
}

// Close closes the batcher and waits for all pending work to complete
func (b *Batcher) Close() {
	// Flush any remaining tasks
	b.Flush()

	// Stop timer loop
	b.cancel()

	// Wait for all goroutines to finish
	b.wg.Wait()

	// Close results channel
	close(b.results)
}
