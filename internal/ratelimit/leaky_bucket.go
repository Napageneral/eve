package ratelimit

import (
	"context"
	"sync"
	"time"
)

// LeakyBucket enforces a smooth, non-bursty request rate.
//
// It schedules each caller at least `interval` after the prior scheduled call,
// even under heavy concurrency. This is useful for RPM-based API quotas where
// spiky bursts cause 429s and/or local network instability.
type LeakyBucket struct {
	mu       sync.Mutex
	interval time.Duration
	next     time.Time
}

func NewLeakyBucketFromRPM(rpm int) *LeakyBucket {
	if rpm <= 0 {
		return nil
	}
	interval := time.Minute / time.Duration(rpm)
	if interval <= 0 {
		interval = time.Nanosecond
	}
	return &LeakyBucket{interval: interval}
}

func (b *LeakyBucket) Wait(ctx context.Context) error {
	if b == nil {
		return nil
	}

	b.mu.Lock()
	now := time.Now()
	if b.next.IsZero() || b.next.Before(now) {
		b.next = now
	}
	wait := b.next.Sub(now)
	b.next = b.next.Add(b.interval)
	b.mu.Unlock()

	if wait <= 0 {
		return nil
	}
	t := time.NewTimer(wait)
	defer t.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
