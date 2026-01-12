package engine

import (
	"context"

	"github.com/Napageneral/eve/internal/queue"
	te "github.com/Napageneral/taskengine/engine"
)

// Re-export engine runtime types from taskengine so Eve's domain-specific handlers
// can keep importing `internal/engine` without churn.
type JobHandler = te.JobHandler
type Engine = te.Engine
type Config = te.Config
type Stats = te.Stats
type FakeJobPayload = te.FakeJobPayload

func DefaultConfig() Config { return te.DefaultConfig() }
func New(q *queue.Queue, config Config) *Engine {
	return te.New(q, config)
}
func FakeJobHandler(ctx context.Context, job *queue.Job) error { return te.FakeJobHandler(ctx, job) }
