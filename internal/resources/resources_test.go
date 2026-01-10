package resources

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadPrompt_EmbeddedFS(t *testing.T) {
	loader := NewLoader("")

	prompt, err := loader.LoadPrompt("convo-all-v1")
	if err != nil {
		t.Fatalf("failed to load convo-all-v1 prompt: %v", err)
	}

	// Check core fields
	if prompt.ID != "convo-all-v1" {
		t.Errorf("expected ID convo-all-v1, got %s", prompt.ID)
	}

	if prompt.Name != "Conversation-Wide Analysis v1" {
		t.Errorf("expected name 'Conversation-Wide Analysis v1', got %s", prompt.Name)
	}

	if prompt.Category != "analysis" {
		t.Errorf("expected category analysis, got %s", prompt.Category)
	}

	// Check body is present
	if len(prompt.Body) == 0 {
		t.Error("expected non-empty body")
	}

	// Check body contains expected content
	if !contains(prompt.Body, "Conversation-Wide Analysis") {
		t.Error("body should contain 'Conversation-Wide Analysis'")
	}

	// Check tags
	expectedTags := []string{"conversation", "analysis", "entities", "topics", "emotions", "humor"}
	if len(prompt.Tags) != len(expectedTags) {
		t.Errorf("expected %d tags, got %d", len(expectedTags), len(prompt.Tags))
	}
}

func TestLoadPack_EmbeddedFS(t *testing.T) {
	loader := NewLoader("")

	pack, err := loader.LoadPack("analyses-all-comprehensive")
	if err != nil {
		t.Fatalf("failed to load analyses-all-comprehensive pack: %v", err)
	}

	// Check core fields
	if pack.ID != "analyses-all-comprehensive" {
		t.Errorf("expected ID analyses-all-comprehensive, got %s", pack.ID)
	}

	if pack.Name != "Analyses - All Time (Comprehensive)" {
		t.Errorf("expected name 'Analyses - All Time (Comprehensive)', got %s", pack.Name)
	}

	if pack.Category != "analysis" {
		t.Errorf("expected category analysis, got %s", pack.Category)
	}

	// Check slices
	if len(pack.Slices) < 2 {
		t.Errorf("expected at least 2 slices, got %d", len(pack.Slices))
	}

	// Check first slice
	firstSlice := pack.Slices[0]
	if firstSlice.Name != "ANALYSES_ALL" {
		t.Errorf("expected first slice name ANALYSES_ALL, got %s", firstSlice.Name)
	}

	if firstSlice.Retrieval != "analyses_context_data" {
		t.Errorf("expected retrieval analyses_context_data, got %s", firstSlice.Retrieval)
	}
}

func TestListPrompts_EmbeddedFS(t *testing.T) {
	loader := NewLoader("")

	prompts, err := loader.ListPrompts()
	if err != nil {
		t.Fatalf("failed to list prompts: %v", err)
	}

	// Should have at least 20 prompts
	if len(prompts) < 20 {
		t.Errorf("expected at least 20 prompts, got %d", len(prompts))
	}

	// Check that convo-all-v1 is in the list
	found := false
	for _, p := range prompts {
		if p.ID == "convo-all-v1" {
			found = true
			break
		}
	}

	if !found {
		t.Error("convo-all-v1 prompt not found in list")
	}
}

func TestListPacks_EmbeddedFS(t *testing.T) {
	loader := NewLoader("")

	packs, err := loader.ListPacks()
	if err != nil {
		t.Fatalf("failed to list packs: %v", err)
	}

	// Should have at least 15 packs
	if len(packs) < 15 {
		t.Errorf("expected at least 15 packs, got %d", len(packs))
	}

	// Check that analyses-all-comprehensive is in the list
	found := false
	for _, p := range packs {
		if p.ID == "analyses-all-comprehensive" {
			found = true
			break
		}
	}

	if !found {
		t.Error("analyses-all-comprehensive pack not found in list")
	}
}

func TestOverrideDirectory(t *testing.T) {
	// Create a temp directory with a custom prompt
	tempDir := t.TempDir()
	promptsDir := filepath.Join(tempDir, "prompts")
	if err := os.MkdirAll(promptsDir, 0755); err != nil {
		t.Fatalf("failed to create temp prompts dir: %v", err)
	}

	// Write a custom prompt
	customPrompt := `---
id: test-custom-v1
name: Test Custom Prompt
category: test
tags: [test]
---

# Test Custom Prompt

This is a custom test prompt.
`
	promptPath := filepath.Join(promptsDir, "test-custom-v1.prompt.md")
	if err := os.WriteFile(promptPath, []byte(customPrompt), 0644); err != nil {
		t.Fatalf("failed to write custom prompt: %v", err)
	}

	// Load with override directory
	loader := NewLoader(tempDir)

	prompt, err := loader.LoadPrompt("test-custom-v1")
	if err != nil {
		t.Fatalf("failed to load custom prompt: %v", err)
	}

	if prompt.ID != "test-custom-v1" {
		t.Errorf("expected ID test-custom-v1, got %s", prompt.ID)
	}

	if prompt.Name != "Test Custom Prompt" {
		t.Errorf("expected name 'Test Custom Prompt', got %s", prompt.Name)
	}

	if !contains(prompt.Body, "custom test prompt") {
		t.Error("body should contain 'custom test prompt'")
	}
}

func contains(s, substr string) bool {
	return len(s) > 0 && len(substr) > 0 && (s == substr || len(s) > len(substr) && findSubstring(s, substr))
}

func findSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
