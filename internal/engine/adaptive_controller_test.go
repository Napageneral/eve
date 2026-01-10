package engine

import (
	"errors"
	"testing"
	"time"
)

func TestAdaptiveController_DecreasesOn429(t *testing.T) {
	sem := NewAdaptiveSemaphore(100)
	cfg := DefaultAdaptiveControllerConfig(100)
	cfg.MinInFlight = 1
	cfg.MaxInFlight = 100
	cfg.DecreaseFactor = 0.7

	c := NewAdaptiveController(sem, cfg)

	// Simulate a window with at least one 429.
	c.Observe(100*time.Millisecond, errors.New("max retries exceeded: retryable status code 429"))
	c.Observe(100*time.Millisecond, nil)

	c.step()

	if got := sem.Limit(); got != 70 {
		t.Fatalf("expected limit to decrease to 70, got %d", got)
	}
}

func TestAdaptiveController_IncreasesWhenHealthy(t *testing.T) {
	sem := NewAdaptiveSemaphore(100)
	cfg := DefaultAdaptiveControllerConfig(100)
	cfg.MinInFlight = 1
	cfg.MaxInFlight = 100
	cfg.IncreasePct = 0.05
	cfg.DecreaseFactor = 0.7

	c := NewAdaptiveController(sem, cfg)
	sem.SetLimit(20)
	c.current = 20

	// Healthy window: no errors.
	for i := 0; i < 50; i++ {
		c.Observe(100*time.Millisecond, nil)
	}
	c.step()

	// step = ceil(20*0.05)=1 => 21
	if got := sem.Limit(); got != 21 {
		t.Fatalf("expected limit to increase to 21, got %d", got)
	}
}

func TestAdaptiveController_LatencyInflationTriggersDecrease(t *testing.T) {
	sem := NewAdaptiveSemaphore(50)
	cfg := DefaultAdaptiveControllerConfig(50)
	cfg.MinInFlight = 1
	cfg.MaxInFlight = 50
	cfg.DecreaseFactor = 0.5
	cfg.FailRateThreshold = 0.5 // don't trip on failures

	c := NewAdaptiveController(sem, cfg)

	// Establish baseline EWMA ~100ms.
	for i := 0; i < 50; i++ {
		c.Observe(100*time.Millisecond, nil)
	}
	c.step()

	// Inflate latency to >2x baseline without errors; should trigger decrease.
	for i := 0; i < 50; i++ {
		c.Observe(1000*time.Millisecond, nil)
	}
	c.step()

	if got := sem.Limit(); got >= 50 {
		t.Fatalf("expected limit to decrease under latency inflation, got %d", got)
	}
}
