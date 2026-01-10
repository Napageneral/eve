package main

import (
	"testing"
)

func TestVersionInfo(t *testing.T) {
	// Basic sanity check that version vars are set
	if version == "" {
		t.Error("version should not be empty")
	}
}
