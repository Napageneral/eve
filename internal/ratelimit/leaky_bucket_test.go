package ratelimit

import (
	"context"
	"testing"
	"time"
)

func TestLeakyBucket_WaitsForRate(t *testing.T) {
	// 1200 RPM = 20 req/s => ~50ms spacing
	b := NewLeakyBucketFromRPM(1200)
	if b == nil {
		t.Fatalf("expected non-nil bucket")
	}

	ctx := context.Background()
	start := time.Now()

	// First wait should be immediate.
	if err := b.Wait(ctx); err != nil {
		t.Fatalf("wait 1: %v", err)
	}
	// Next two waits should cost ~100ms total (2 * 50ms), allow slack.
	if err := b.Wait(ctx); err != nil {
		t.Fatalf("wait 2: %v", err)
	}
	if err := b.Wait(ctx); err != nil {
		t.Fatalf("wait 3: %v", err)
	}

	elapsed := time.Since(start)
	if elapsed < 80*time.Millisecond {
		t.Fatalf("expected rate-limited waits, got elapsed=%s", elapsed)
	}
}
