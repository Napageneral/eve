package engine

import (
	"encoding/json"
	"sync"
	"time"
)

// AnalysisMetrics captures coarse-grained timing for convo analysis jobs.
// It is intentionally lightweight and aggregated (no per-conversation data).
type AnalysisMetrics struct {
	mu sync.Mutex

	JobsTotal   int
	JobsOK      int
	JobsBlocked int
	JobsError   int

	BlockedReasonCounts map[string]int

	TotalDBRead   time.Duration
	TotalEncode   time.Duration
	TotalPrompt   time.Duration
	TotalAPICall  time.Duration
	TotalParse    time.Duration
	TotalDBWrite  time.Duration
	TotalOverall  time.Duration
}

func NewAnalysisMetrics() *AnalysisMetrics {
	return &AnalysisMetrics{
		BlockedReasonCounts: make(map[string]int),
	}
}

type AnalysisMetricEvent struct {
	DBRead  time.Duration
	Encode  time.Duration
	Prompt  time.Duration
	APICall time.Duration
	Parse   time.Duration
	DBWrite time.Duration
	Overall time.Duration

	Outcome      string // "ok" | "blocked" | "error"
	BlockedReason string
}

func (m *AnalysisMetrics) Record(ev AnalysisMetricEvent) {
	if m == nil {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	m.JobsTotal++
	switch ev.Outcome {
	case "ok":
		m.JobsOK++
	case "blocked":
		m.JobsBlocked++
		if ev.BlockedReason != "" {
			m.BlockedReasonCounts[ev.BlockedReason]++
		}
	default:
		m.JobsError++
	}

	m.TotalDBRead += ev.DBRead
	m.TotalEncode += ev.Encode
	m.TotalPrompt += ev.Prompt
	m.TotalAPICall += ev.APICall
	m.TotalParse += ev.Parse
	m.TotalDBWrite += ev.DBWrite
	m.TotalOverall += ev.Overall
}

func (m *AnalysisMetrics) SnapshotJSON() json.RawMessage {
	if m == nil {
		return json.RawMessage("null")
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	div := func(d time.Duration, n int) float64 {
		if n <= 0 {
			return 0
		}
		return float64(d.Milliseconds()) / float64(n)
	}

	out := map[string]any{
		"jobs_total":   m.JobsTotal,
		"jobs_ok":      m.JobsOK,
		"jobs_blocked": m.JobsBlocked,
		"jobs_error":   m.JobsError,
		"avg_ms": map[string]any{
			"db_read":   div(m.TotalDBRead, m.JobsTotal),
			"encode":    div(m.TotalEncode, m.JobsTotal),
			"prompt":    div(m.TotalPrompt, m.JobsTotal),
			"api_call":  div(m.TotalAPICall, m.JobsTotal),
			"parse":     div(m.TotalParse, m.JobsTotal),
			"db_write":  div(m.TotalDBWrite, m.JobsTotal),
			"overall":   div(m.TotalOverall, m.JobsTotal),
		},
		"blocked_reason_counts": m.BlockedReasonCounts,
	}

	b, _ := json.Marshal(out)
	return b
}

