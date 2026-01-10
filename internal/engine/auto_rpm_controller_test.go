package engine

import (
	"errors"
	"testing"
	"time"
)

func TestAutoRPMController_IncreasesWhenHealthy(t *testing.T) {
	var setTo []int
	c := NewAutoRPMController(AutoRPMConfig{
		MinRPM:            100,
		MaxRPM:            100000,
		StartRPM:          500,
		Tick:              1 * time.Second,
		SlowStartFactor:   2.0,
		SlowStartUntilRPM: 4000,
		DecreaseFactor:    0.7,
		IncreaseFactor:    1.1,
	}, func(rpm int) { setTo = append(setTo, rpm) })

	// Give it a healthy window.
	for i := 0; i < 10; i++ {
		c.Observe(nil)
	}
	c.step()

	if got := c.CurrentRPM(); got != 1000 { // slow-start: 500*2
		t.Fatalf("expected rpm to increase to 1000, got %d", got)
	}
	if len(setTo) == 0 || setTo[len(setTo)-1] != 1000 {
		t.Fatalf("expected setter to be called with 1000, got %v", setTo)
	}
}

func TestAutoRPMController_DecreasesOn429(t *testing.T) {
	c := NewAutoRPMController(DefaultAutoRPMConfig(), func(int) {})
	// Force starting rpm for test determinism
	c.current = 1000

	c.Observe(errors.New("max retries exceeded: retryable status code 429"))
	c.Observe(nil)
	c.step()

	// floor(1000*0.6)=600
	if got := c.CurrentRPM(); got != 600 {
		t.Fatalf("expected rpm to decrease to 600, got %d", got)
	}
}
