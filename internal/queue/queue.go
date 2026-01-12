package queue

import (
	"database/sql"
	"time"

	tq "github.com/Napageneral/taskengine/queue"
)

// Re-export core queue types from taskengine for backward compatibility inside Eve.
type Job = tq.Job
type Queue = tq.Queue
type EnqueueOptions = tq.EnqueueOptions
type LeaseOptions = tq.LeaseOptions
type FailOptions = tq.FailOptions
type Stats = tq.Stats

// Keep time imported in this package's public surface (matches prior API usage).
var _ time.Duration

func Init(db *sql.DB) error { return tq.Init(db) }
func New(db *sql.DB) *Queue { return tq.New(db) }
